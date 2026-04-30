#!/usr/bin/env python3
"""
推送引擎：为指定学生生成个性化题目推送。

变更：
- 接入 students/roster.json：自动获取学生的 teacher_id
- 按 teacher_id 过滤题库，不跨科目推送
- 拦截老师账号（role=teacher），拒绝接收题目
- 未绑定老师的学生返回友好提示，不推送
- 当天已推送时返回"今日题目已发送，明日再来"

用法：
    python push_engine.py --student stu_001
    python push_engine.py --student ou_xxx --pretty
"""

import json
import argparse
import random
from datetime import date, datetime
from pathlib import Path

from config import (
    PUSH_COUNT,
    DEDUP_DAYS,
    MASTERY_THRESHOLD,
    STUDENTS_DIR,
)
from logger import log_push_event
from student_store import (
    load_student, save_student, save_context as _store_save_context,
    load_roster, get_student_bindings, questions_json as _questions_json_path,
)

# 记忆写入（非阻塞，失败不影响主流程）
try:
    from memory_store import update_learning_progress
    _MEMORY_ENABLED = True
except ImportError:
    _MEMORY_ENABLED = False

ROSTER_JSON = Path(STUDENTS_DIR) / "roster.json"


# ── 数据加载 / 保存 ───────────────────────────────────────────

def _write_push_memory(student: dict, selected: list[dict], weak_topics: list[str]) -> None:
    """
    推题后写入学习进展记忆。
    只在以下情形记录：有薄弱点推送、难度档位等关键节点。
    """
    student_id = student["student_id"]
    student_name = student.get("name", student_id)
    difficulty = student.get("current_difficulty", 3)

    # 构建推送摘要
    topic_tags = []
    for q in selected:
        topic_tags.extend(q.get("topic_tags", []))
    unique_topics = list(dict.fromkeys(topic_tags))  # 去重保序

    lines = [
        f"推送 {len(selected)} 道题（难度档 {difficulty}）",
    ]
    if unique_topics:
        lines.append(f"涉及知识点：{', '.join(f'`{t}`' for t in unique_topics[:5])}")
    if weak_topics:
        lines.append(f"本次侧重薄弱点：{', '.join(f'`{t}`' for t in weak_topics[:3])}")

    progress_text = "，".join(lines[:1]) + "\n\n" + "\n\n".join(lines[1:])

    update_learning_progress(student_id, progress_text, student_name=student_name)


def load_questions_for_teacher(teacher_id: str) -> list[dict]:
    """按 teacher_id 加载对应题库，返回题目列表。"""
    from config import questions_json
    p = questions_json(teacher_id)
    if not p.exists():
        raise FileNotFoundError(
            f"老师 {teacher_id} 的题库不存在: {p}\n"
            "请先运行 process_pdf.py --teacher-id {teacher_id} --parse-questions 生成题库。"
        )
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_roster() -> dict:
    """加载学生花名册 students/roster.json（新格式）"""
    if not ROSTER_JSON.exists():
        return {"teachers": {}, "students": {}}
    with open(ROSTER_JSON, encoding="utf-8") as f:
        return json.load(f)


# ── 去重过滤 ──────────────────────────────────────────────────

def _recent_sent_ids(send_history: list[dict], dedup_days: int | None = None) -> set[int]:
    """返回 dedup_days 天内已推送的题目 ID 集合。
    dedup_days 为 None 时降级使用全局 DEDUP_DAYS。"""
    effective_days = DEDUP_DAYS if dedup_days is None else dedup_days
    today = date.today()
    ids: set[int] = set()
    for record in send_history:
        try:
            sent_date = date.fromisoformat(record.get("sent_at", ""))
        except ValueError:
            continue
        if (today - sent_date).days < effective_days:
            ids.add(record["question_id"])
    return ids


def _ever_sent_wrong_ids(send_history: list[dict]) -> set[int]:
    """返回所有曾推送但答错过的题目 ID（用于二刷补充）"""
    return {
        r["question_id"]
        for r in send_history
        if r.get("answered") and r.get("is_correct") is False
    }


# ── 优先级评分 ────────────────────────────────────────────────

def score_question(q: dict, student: dict) -> float:
    """
    计算题目对该学生的优先级分数（越高越优先推送）。

        score = 0.4 × topic_gap_score
              + 0.2 × weak_topic_hit_ratio
              + 0.4 × difficulty_match_score
    """
    topic_mastery = student.get("topic_mastery", {})
    weak_topics   = set(student.get("weak_topics", []))
    current_diff  = student.get("current_difficulty", 3)

    q_tags = q.get("topic_tags", [])

    # 1. 知识点掌握度差距分
    if q_tags:
        gap_sum = 0.0
        for tag in q_tags:
            mastery = topic_mastery.get(tag, 0.5)
            if mastery < MASTERY_THRESHOLD:
                gap_sum += (MASTERY_THRESHOLD - mastery) * 2
            else:
                gap_sum += max(0.0, MASTERY_THRESHOLD - mastery)
        topic_gap_score = gap_sum / len(q_tags)
    else:
        topic_gap_score = 0.3

    # 2. 薄弱点命中率
    weak_hit = len(set(q_tags) & weak_topics) / len(q_tags) if q_tags else 0.0

    # 3. 难度匹配分
    diff_gap         = abs(q.get("difficulty", 3) - current_diff)
    difficulty_score = 1.0 - diff_gap / 4.0

    return 0.4 * topic_gap_score + 0.2 * weak_hit + 0.4 * difficulty_score


# ── 选题逻辑 ──────────────────────────────────────────────────

def select_questions(student: dict, questions: list[dict], push_count: int = PUSH_COUNT, dedup_days: int | None = None) -> list[dict]:
    """
    按优先级选取 push_count 道题目（题库已按 teacher_id 过滤）。
    优先使用未推送或超出去重窗口的题目；
    不足时用曾推送但答错的题目补充（二刷）。
    当评分相同时随机选择，增加多样性。
    dedup_days 为 None 时使用全局 DEDUP_DAYS。
    """
    send_history = student.get("send_history", [])
    recent_ids   = _recent_sent_ids(send_history, dedup_days=dedup_days)
    wrong_ids    = _ever_sent_wrong_ids(send_history)

    fresh        = [q for q in questions if q["id"] not in recent_ids]
    fresh_sorted = sorted(fresh, key=lambda q: score_question(q, student), reverse=True)

    selected = []
    if fresh_sorted:
        top_score = score_question(fresh_sorted[0], student)
        top_questions = [q for q in fresh_sorted if score_question(q, student) >= top_score - 0.01]
        random.shuffle(top_questions)
        selected = top_questions[:push_count]

    if len(selected) < push_count:
        selected_ids = {q["id"] for q in selected}
        review       = [
            q for q in questions
            if q["id"] in wrong_ids
            and q["id"] not in recent_ids
            and q["id"] not in selected_ids
        ]
        review_sorted = sorted(review, key=lambda q: score_question(q, student), reverse=True)
        selected.extend(review_sorted[:push_count - len(selected)])

    return selected


# ── 对话上下文（供答题匹配用）────────────────────────────────

def _save_context(student_id: str, pushed_ids: list[int]) -> None:
    """推送完成后将 last_pushed_question_ids 写入学生 context 文件。"""
    _store_save_context(student_id, pushed_ids)


# ── 推送记录 & 输出格式 ───────────────────────────────────────

def record_push(student: dict, questions: list[dict]) -> dict:
    """将推送的题目写入学生 send_history，并返回推送结果 JSON。"""
    today = str(date.today())

    for q in questions:
        student["send_history"].append({
            "question_id":      q["id"],
            "sent_at":          today,
            "answered":         False,
            "is_correct":       None,
            "submitted_answer": None,
        })

    return {
        "student_id":  student["student_id"],
        "teacher_id":  student.get("teacher_id", ""),
        "push_date":   today,
        "questions": [
            {
                "question_id":    q["id"],
                "question_image": q.get("question_image"),
                "answer_image":   q.get("answer_image"),
                "correct_answer": q.get("correct_answer"),
                "topic_tags":     q.get("topic_tags", []),
                "difficulty":     q.get("difficulty"),
                "question_text":  q.get("question_text", ""),
                "answer_text":    q.get("answer_text", ""),
                "options":        q.get("options", {}),
            }
            for q in questions
        ],
    }


# ── 主流程 ────────────────────────────────────────────────────

def push(student_id: str, chat_id: str = "") -> dict:
    """
    加载 → 校验身份（roster 新结构）→ 按 teacher_id 过滤题库
    → 选题 → 记录推送历史 → 返回推送结果

    Args:
        student_id: 学生 open_id。
        chat_id:    消息来源群 chat_id，用于从 bindings 中确定上下文老师。
                    为空时取学生第一条 binding（单群场景向后兼容）。
    """
    today  = str(date.today())
    roster = load_roster()

    students = roster.get("students", {})
    student_entry = students.get(student_id, {})

    # 拦截：open_id 属于老师
    teachers = roster.get("teachers", {})
    for tid, tinfo in teachers.items():
        if tinfo.get("open_id") == student_id:
            return {
                "student_id": student_id,
                "push_date":  today,
                "questions":  [],
                "error":      "该账号为老师账号，不支持接收题目推送",
            }

    # 拦截：推题开关已关闭
    if not student_entry.get("push_enabled", True):
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "message":    "该学生推题已暂停",
        }

    # 从 bindings 中找到当前 chat_id 对应的 teacher
    bindings = student_entry.get("bindings", [])
    if not bindings:
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "error":      f"学生 {student_id} 尚未绑定老师，请管理员在 students/roster.json 中配置 bindings",
        }

    # 根据 chat_id 选择对应 binding，找不到则取第一条（兼容单群场景）
    binding = None
    if chat_id:
        for b in bindings:
            if b.get("chat_id") == chat_id:
                binding = b
                break
    if binding is None:
        binding = bindings[0]

    teacher_id = binding.get("teacher_id", "")
    if not teacher_id:
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "error":      "binding 中缺少 teacher_id，请检查 roster.json",
        }

    # 按 teacher_id 加载对应题库
    try:
        all_questions = load_questions_for_teacher(teacher_id)
    except FileNotFoundError as e:
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "error":      str(e),
        }

    questions = [q for q in all_questions if q.get("teacher_id") == teacher_id]
    if not questions:
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "error":      f"老师 {teacher_id} 的题库为空，请先运行 process_pdf.py --teacher-id {teacher_id} 解析题库",
        }

    student = load_student(student_id)

    # 将 roster 信息同步到学生画像（首次创建时补充）
    if not student.get("teacher_id"):
        student["teacher_id"] = teacher_id
        student["subject"]    = binding.get("subject", "")
        student["name"]       = student_entry.get("name", student_id)

    selected = select_questions(student, questions,
                                push_count=roster.get("teachers", {}).get(teacher_id, {}).get("push_count", PUSH_COUNT),
                                dedup_days=roster.get("teachers", {}).get(teacher_id, {}).get("dedup_days"))

    if not selected:
        return {
            "student_id": student_id,
            "push_date":  today,
            "questions":  [],
            "message":    "当前题库题目已全部推送且在去重窗口内，请等待题库扩充或窗口刷新",
        }

    result = record_push(student, selected)
    _save_context(student_id, [q["id"] for q in selected])
    save_student(student)

    # 记录推送日志
    weak_topics = student.get("weak_topics", [])
    push_reason = "weak_topic" if weak_topics else "daily_push"
    log_push_event(
        student_id=student_id,
        question_ids=[q["id"] for q in selected],
        difficulty=student.get("current_difficulty", 3),
        weak_topics=weak_topics,
        chat_id=chat_id or None,
        teacher_id=teacher_id,
        push_reason=push_reason,
    )

    # ── 事件驱动记忆写入 ─────────────────────────────────────
    if _MEMORY_ENABLED:
        try:
            _write_push_memory(student, selected, weak_topics)
        except Exception:
            pass  # 记忆写入失败不阻断主流程

    return result


def load_questions() -> list[dict]:
    """加载所有老师的题库，合并返回（question id 唯一）。"""
    roster = load_roster()
    all_q: list[dict] = []
    for teacher_id in roster.get("teachers", {}):
        try:
            all_q.extend(load_questions_for_teacher(teacher_id))
        except FileNotFoundError:
            pass
    return all_q


def push_manual(student_id: str, chapter: str | None = None,
                difficulty: int | None = None,
                question_ids: list[int] | None = None,
                chat_id: str = "") -> dict:
    """
    手动推题：支持按章节、难度、指定题目 ID 过滤后推送。
    不受去重窗口限制，不更新 send_history。
    """
    today = str(date.today())
    roster = load_roster()
    student = load_student(student_id)

    # 确定 teacher_id
    student_entry = roster.get("students", {}).get(student_id, {})
    bindings = student_entry.get("bindings", [])
    binding = next((b for b in bindings if not chat_id or b.get("chat_id") == chat_id),
                   bindings[0] if bindings else {})
    teacher_id = binding.get("teacher_id", student.get("teacher_id", ""))

    if not teacher_id:
        return {"student_id": student_id, "push_date": today, "questions": [],
                "error": "未找到绑定老师"}

    try:
        all_questions = load_questions_for_teacher(teacher_id)
    except FileNotFoundError as e:
        return {"student_id": student_id, "push_date": today, "questions": [], "error": str(e)}

    pool = [q for q in all_questions if q.get("teacher_id") == teacher_id]

    if question_ids is not None:
        id_set = set(question_ids)
        pool = [q for q in pool if q["id"] in id_set]
    if chapter is not None:
        pool = [q for q in pool if q.get("chapter", "") == chapter]
    if difficulty is not None:
        pool = [q for q in pool if q.get("difficulty") == difficulty]

    if not pool:
        return {"student_id": student_id, "push_date": today, "questions": [],
                "message": "过滤后无匹配题目"}

    push_count = roster.get("teachers", {}).get(teacher_id, {}).get("push_count", PUSH_COUNT)
    selected = pool[:push_count]

    return {
        "student_id": student_id,
        "teacher_id": teacher_id,
        "push_date":  today,
        "manual":     True,
        "questions": [
            {
                "question_id":    q["id"],
                "question_image": q.get("question_image"),
                "answer_image":   q.get("answer_image"),
                "correct_answer": q.get("correct_answer"),
                "topic_tags":     q.get("topic_tags", []),
                "difficulty":     q.get("difficulty"),
                "chapter":        q.get("chapter", ""),
                "options":        q.get("options", {}),
            }
            for q in selected
        ],
    }


def list_questions() -> list[dict]:
    """输出 questions.json 中所有题目的精简列表（供 TypeScript 端调用）"""
    questions = load_questions()
    return [
        {
            "question_id":    q["id"],
            "question_image": q.get("question_image"),
            "answer_image":   q.get("answer_image"),
            "teacher_id":     q.get("teacher_id", ""),
        }
        for q in questions
    ]


def main():
    parser = argparse.ArgumentParser(description="AP 题目个性化推送引擎")
    parser.add_argument("--student",        help="学生 ID（如 stu_001 或飞书 open_id）")
    parser.add_argument("--pretty",         action="store_true", help="格式化输出 JSON")
    parser.add_argument("--list-questions", action="store_true",
                        help="输出 questions.json 精简列表（question_id/question_image/answer_image）")
    args = parser.parse_args()

    if args.list_questions:
        result = list_questions()
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return

    if not args.student:
        parser.error("--student 是必填参数（除非使用 --list-questions）")

    result = push(args.student)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
