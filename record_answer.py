#!/usr/bin/env python3
"""
答题反馈记录：学生提交答案后更新画像（掌握度、难度档位、薄弱点）。

用法：
    python record_answer.py --student stu_001 --question 5 --answer A --correct true
    python record_answer.py --student stu_001 --question 5 --answer B --correct false
"""

import json
import argparse
from datetime import datetime
from pathlib import Path

from config import (
    STUDENTS_DIR,
    MASTERY_THRESHOLD,
    DIFFICULTY_UP_THRESHOLD,
    DIFFICULTY_DOWN_THRESHOLD,
    QUESTIONS_JSON,
)
from logger import log_grading_event
from student_store import load_student, save_student


# ── 数据加载 / 保存 ───────────────────────────────────────────

def _load_all_questions() -> dict[int, dict]:
    """从 questions.json 构建 {id: question} 映射"""
    p = Path(QUESTIONS_JSON)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {q["id"]: q for q in json.load(f)}


# ── 答题记录更新 ──────────────────────────────────────────────

def update_answer_record(
    student: dict,
    question_id: int,
    submitted_answer: str,
    is_correct: bool,
):
    """
    找到 send_history 中最近一条该题未回答的发送记录并填写答题结果。
    若找不到（如直接测试调用），则追加一条新记录。
    """
    answered_at = datetime.now().isoformat()

    for record in reversed(student["send_history"]):
        if record["question_id"] == question_id and not record.get("answered"):
            record["answered"]         = True
            record["is_correct"]       = is_correct
            record["submitted_answer"] = submitted_answer
            record["answered_at"]      = answered_at
            return

    # 防御性追加（题目未经 push_engine 推送直接记录）
    student["send_history"].append({
        "question_id":      question_id,
        "sent_at":          answered_at[:10],
        "answered":         True,
        "is_correct":       is_correct,
        "submitted_answer": submitted_answer,
        "answered_at":      answered_at,
    })


# ── 知识点掌握度重算 ──────────────────────────────────────────

def recalculate_topic_mastery(student: dict, question: dict | None, questions_db: dict[int, dict]):
    """
    对该题涉及的每个知识点，重新计算学生历史正确率并更新 topic_mastery。
    掌握度 = 该知识点所有已回答题目的正确率（简单平均）。
    """
    if not question:
        return

    topic_tags = question.get("topic_tags", [])
    if not topic_tags:
        return

    # 构建 已答题目ID → 是否正确 的映射
    answered_map: dict[int, bool] = {
        r["question_id"]: r["is_correct"]
        for r in student["send_history"]
        if r.get("answered") and r.get("is_correct") is not None
    }

    for tag in topic_tags:
        results = []
        for q_id, correct in answered_map.items():
            q = questions_db.get(q_id)
            if q and tag in q.get("topic_tags", []):
                results.append(1 if correct else 0)

        if results:
            student["topic_mastery"][tag] = round(sum(results) / len(results), 3)


def update_weak_topics(student: dict):
    """将掌握度低于阈值的知识点列入 weak_topics"""
    student["weak_topics"] = [
        tag
        for tag, mastery in student.get("topic_mastery", {}).items()
        if mastery < MASTERY_THRESHOLD
    ]


# ── 难度自适应 ────────────────────────────────────────────────

def adapt_difficulty(student: dict):
    """
    基于近 10 道已回答题目的正确率自动调整难度档位：
        ≥ DIFFICULTY_UP_THRESHOLD   → 提升一档（最高 5）
        < DIFFICULTY_DOWN_THRESHOLD → 降低一档（最低 1）
        其余                        → 维持不变
    数据不足 10 条时不做调整。
    """
    recent = [
        r for r in student["send_history"]
        if r.get("answered") and r.get("is_correct") is not None
    ][-10:]

    if len(recent) < 10:
        return

    accuracy = sum(1 for r in recent if r["is_correct"]) / len(recent)
    current  = student.get("current_difficulty", 3)

    if accuracy >= DIFFICULTY_UP_THRESHOLD:
        student["current_difficulty"] = min(current + 1, 5)
    elif accuracy < DIFFICULTY_DOWN_THRESHOLD:
        student["current_difficulty"] = max(current - 1, 1)


# ── 主流程 ────────────────────────────────────────────────────

def record(
    student_id: str,
    question_id: int,
    submitted_answer: str,
    is_correct: bool,
    chat_id: str = None,
    teacher_id: str = None,
) -> dict:
    """
    完整流程：
        1. 更新答题记录
        2. 重新计算知识点掌握度
        3. 更新薄弱点列表
        4. 自适应难度
        5. 记录日志
        6. 保存学生画像
        7. 返回更新摘要
    """
    student      = load_student(student_id)
    questions_db = _load_all_questions()
    question     = questions_db.get(question_id)

    # 获取更新前的状态
    mastery_before = student.get("topic_mastery", {}).get(question.get("topic_tags", [None])[0]) if question else None
    difficulty_before = student.get("current_difficulty", 3)

    update_answer_record(student, question_id, submitted_answer, is_correct)
    recalculate_topic_mastery(student, question, questions_db)
    update_weak_topics(student)
    adapt_difficulty(student)

    # 获取更新后的状态
    mastery_after = student.get("topic_mastery", {}).get(question.get("topic_tags", [None])[0]) if question else None
    difficulty_after = student.get("current_difficulty", 3)
    correct_answer = question.get("correct_answer") if question else None
    topic = question.get("topic_tags", [None])[0] if question else None

    # 记录日志
    log_grading_event(
        student_id=student_id,
        question_id=question_id,
        student_answer=submitted_answer,
        correct_answer=correct_answer,
        is_correct=is_correct,
        topic=topic,
        mastery_before=mastery_before,
        mastery_after=mastery_after,
        difficulty_before=difficulty_before,
        difficulty_after=difficulty_after,
        chat_id=chat_id,
        teacher_id=teacher_id,
    )

    save_student(student)

    return {
        "student_id":        student_id,
        "question_id":       question_id,
        "submitted_answer":  submitted_answer,
        "is_correct":        is_correct,
        "current_difficulty": student["current_difficulty"],
        "weak_topics":       student["weak_topics"],
        "topic_mastery":     student.get("topic_mastery", {}),
    }


def main():
    parser = argparse.ArgumentParser(description="记录学生答题结果并更新画像")
    parser.add_argument("--student",  required=True,       help="学生 ID（如 stu_001）")
    parser.add_argument("--question", required=True, type=int, help="题目 ID（整数）")
    parser.add_argument("--answer",   required=True,       help="学生提交的答案（如 A/B/C/D 或数值）")
    parser.add_argument("--correct",  required=True,       help="是否答对（true / false）")
    args = parser.parse_args()

    is_correct = args.correct.strip().lower() in ("true", "1", "yes")

    result = record(args.student, args.question, args.answer, is_correct)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
