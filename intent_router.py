#!/usr/bin/env python3
"""
intent_router.py

调用 Claude API 解析学生飞书消息的意图，路由到对应功能模块。

意图分类：
  push_questions    — 出题 / 推题
  show_answer       — 查看解析 / 上题答案
  adjust_difficulty — 调整难度（简单点 / 难一点）
  show_progress     — 查看进度 / 学习报告
  record_answer     — 提交答案（A/B/C/D）
  unknown           — 无法识别
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI
from config import UNIAPI_KEY, UNIAPI_BASE
from student_store import load_context as _load_context, save_context

# ── 类型定义 ──────────────────────────────────────────────────

IntentType = Literal[
    "push_questions",
    "show_answer",
    "adjust_difficulty",
    "show_progress",
    "show_logs",
    "record_answer",
    "unknown",
]


@dataclass
class Intent:
    type: IntentType
    # 附加参数，按意图类型不同而不同
    answer: str | None = None          # record_answer: 学生提交的选项 A/B/C/D
    difficulty_delta: int | None = None  # adjust_difficulty: +1 或 -1
    raw: str = ""                      # 原始消息，供调试


# ── Claude 客户端 ─────────────────────────────────────────────

_client = OpenAI(api_key=UNIAPI_KEY, base_url=UNIAPI_BASE)

_SYSTEM_PROMPT = """\
你是一个 AP 统计家教机器人的意图识别模块。
学生会用中文或英文发消息，你需要判断消息属于哪种意图，并返回 JSON。

意图类型及判断规则：
- push_questions：学生想要做题、出题、练习某个知识点
  示例："出题"、"给我出几道题"、"我想练假设检验"、"来道难题"
- show_answer：学生想看上一道题或某道题的解析/答案
  示例："解析"、"上题答案"、"第5题的解析"、"我不懂这道题"
- adjust_difficulty：学生想调整题目难度
  示例："简单点"、"难一点"、"降低难度"、"出难度2的题"
  需要额外返回 difficulty_delta：降低为 -1，提升为 +1
- show_progress：学生想查看自己的学习进度或报告
  示例："我的进度"、"学习报告"、"我哪里比较弱"、"正确率多少"
- show_logs：学生想查看学习日志或答题历史
  示例："查看日志"、"我的学习记录"、"答题历史"、"看看我做过什么题"
- record_answer：学生在提交答案，消息主体是一个或多个选项字母
  示例（单题）："A"、"选B"、"我选C"、"答案是D"、"B吧"
  示例（多题批量）："答案是A、B、D"、"A B C"、"ABD"、"1A2B3C"、
                   "第一题A第二题B"、"答案分别是abb"、"选A,B,C"
  需要额外返回 answer：单题时返回大写字母 A/B/C/D/E，多题时返回第一题答案
- unknown：无法归入以上任何类别

只返回 JSON，不要有任何其他文字。格式：
{"intent": "<类型>", "answer": "<A|B|C|D|null>", "difficulty_delta": <1|-1|null>}
"""


def classify_intent(message: str) -> Intent:
    """
    调用 Claude API 识别消息意图。
    网络失败或解析失败时降级为规则匹配。
    """
    try:
        resp = _client.chat.completions.create(
            model="claude-sonnet-4-5",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            max_tokens=100,
            temperature=0,
        )
        raw_json = resp.choices[0].message.content.strip()
        data = json.loads(raw_json)
        intent_type = data.get("intent", "unknown")
        if intent_type not in (
            "push_questions", "show_answer", "adjust_difficulty",
            "show_progress", "show_logs", "record_answer", "unknown"
        ):
            intent_type = "unknown"
        return Intent(
            type=intent_type,
            answer=data.get("answer") or None,
            difficulty_delta=data.get("difficulty_delta") or None,
            raw=message,
        )
    except Exception:
        return _rule_based_fallback(message)


def _rule_based_fallback(message: str) -> Intent:
    """规则兜底，不依赖 API。"""
    msg = message.strip().upper()

    # 单字母答案（AP 统计有 A-E 五选项）
    if re.fullmatch(r"[ABCDE]", msg):
        return Intent(type="record_answer", answer=msg, raw=message)

    # 带题号格式：第1题A、1.A、①A（fallback 路径）
    m_num = re.search(
        r"(?:第\s*\d+\s*题|\d+[.)、]|[①②③④⑤⑥⑦⑧⑨⑩])\s*[选答是：:为]?\s*([ABCDE])",
        message, re.IGNORECASE,
    )
    if m_num:
        return Intent(type="record_answer", answer=m_num.group(1).upper(), raw=message)

    # 含选项关键词
    m = re.search(r"选([ABCDE])|答案.*?([ABCDE])|([ABCDE])吧", message, re.IGNORECASE)
    if m:
        ans = (m.group(1) or m.group(2) or m.group(3)).upper()
        return Intent(type="record_answer", answer=ans, raw=message)

    # 难度调整
    if any(k in message for k in ["简单", "降低难度", "难度低", "容易点"]):
        return Intent(type="adjust_difficulty", difficulty_delta=-1, raw=message)
    if any(k in message for k in ["难一点", "提升难度", "难度高", "挑战"]):
        return Intent(type="adjust_difficulty", difficulty_delta=+1, raw=message)

    # 解析
    if any(k in message for k in ["解析", "答案", "不懂", "讲一下"]):
        return Intent(type="show_answer", raw=message)

    # 进度
    if any(k in message for k in ["进度", "报告", "正确率", "薄弱", "哪里弱"]):
        return Intent(type="show_progress", raw=message)

    # 日志
    if any(k in message for k in ["日志", "记录", "历史", "做过", "答题"]):
        return Intent(type="show_logs", raw=message)

    # 出题（放最后，避免误匹配）
    if any(k in message for k in ["出题", "做题", "练", "题目", "来题", "推题"]):
        return Intent(type="push_questions", raw=message)

    return Intent(type="unknown", raw=message)


# ── 路由分发 ──────────────────────────────────────────────────

def route(student_id: str, message: str, chat_id: str = "") -> dict:
    """
    识别意图并路由到对应处理逻辑。
    返回供飞书 Bot 直接发送的响应字典：
      { "reply": str, "push_result": dict | None }

    Args:
        student_id: 学生 open_id。
        message:    学生原始消息（已剥离飞书标签）。
        chat_id:    消息来源群 chat_id，透传给 push_engine 写入日志。
    """
    intent = classify_intent(message)

    if intent.type == "push_questions":
        return _handle_push(student_id, intent, chat_id=chat_id)

    if intent.type == "record_answer":
        return _handle_record_answer(student_id, intent)

    if intent.type == "show_answer":
        return _handle_show_answer(student_id, intent)

    if intent.type == "adjust_difficulty":
        return _handle_adjust_difficulty(student_id, intent)

    if intent.type == "show_progress":
        return _handle_show_progress(student_id, intent)

    if intent.type == "show_logs":
        return _handle_show_logs(student_id, intent)

    return {"reply": "我没太明白你的意思，可以说「出题」、「选A」、「看解析」或「我的进度」。", "push_result": None}


# ── 各意图处理函数 ────────────────────────────────────────────

def _handle_push(student_id: str, intent: Intent, chat_id: str = "") -> dict:
    import push_engine
    result = push_engine.push(student_id, chat_id=chat_id)
    if not result.get("questions"):
        return {"reply": result.get("message", "今日题目已发送，明日再来~"), "push_result": None}
    return {"reply": None, "push_result": result}


def _handle_record_answer(student_id: str, intent: Intent) -> dict:
    import record_answer
    import push_engine
    from answer_parser import parse_answers, AnswerMatch

    # 用 answer_parser 精确匹配题目（支持多题批量提交）
    matches: list[AnswerMatch] = parse_answers(student_id, intent.raw)

    # 若 answer_parser 未能匹配，降级用 intent.answer + 最后一题
    if not matches:
        if not intent.answer:
            return {"reply": "没有识别到你的答案，请直接回复 A、B、C 或 D。", "push_result": None}
        ctx = _load_context(student_id)
        last_ids = ctx.get("last_pushed_question_ids", [])
        if not last_ids:
            from answer_parser import _fallback_ids_from_profile
            last_ids = _fallback_ids_from_profile(student_id)
        if not last_ids:
            return {"reply": "找不到最近推送的题目，请先让我出题。", "push_result": None}
        from answer_parser import AnswerMatch
        matches = [AnswerMatch(question_id=last_ids[-1], answer=intent.answer)]

    questions = push_engine.load_questions()
    q_map = {q["id"]: q for q in questions}

    reply_lines: list[str] = []
    wrong_ids: list[int] = []
    answer_images: list[str] = []
    answered_ids: list[int] = []

    for m in matches:
        q = q_map.get(m.question_id)
        if not q:
            continue
        answered_ids.append(m.question_id)
        correct = (q.get("correct_answer") or "").upper()
        is_correct = m.answer.upper() == correct
        record_answer.record(student_id, m.question_id, m.answer, is_correct)

        if is_correct:
            reply_lines.append(f"题目 {m.question_id}：✅ 正确（{correct}）")
        else:
            reply_lines.append(f"题目 {m.question_id}：❌ 你选了 {m.answer}，正确答案是 {correct}")
            wrong_ids.append(m.question_id)
            # 收集错题的解析图片路径
            answer_img = q.get("answer_image")
            if answer_img:
                answer_images.append(answer_img)

    if not reply_lines:
        return {"reply": "找不到对应题目信息。", "push_result": None}

    reply = "\n".join(reply_lines)

    # 对最后一道错题生成 AI 分析
    if wrong_ids:
        last_wrong_id = wrong_ids[-1]
        wrong_match = next((m for m in matches if m.question_id == last_wrong_id), None)
        if wrong_match:
            try:
                from error_analyzer import analyze_error
                analysis = analyze_error(student_id, last_wrong_id, wrong_match.answer)
                reply += f"\n\n{analysis}"
            except Exception:
                pass
        if len(wrong_ids) > 1:
            reply += f"\n\n错题解析图片（共{len(wrong_ids)}道）👇"
        else:
            reply += "\n\n解析图片 👇"

    return {
        "reply": reply,
        "push_result": None,
        "show_answer_for": wrong_ids[-1] if wrong_ids else None,
        "show_answer_for_all": wrong_ids if wrong_ids else None,
        "answer_images": answer_images,
        "is_correct": len(wrong_ids) == 0,
    }


def _handle_show_answer(student_id: str, intent: Intent) -> dict:
    """处理查看解析的请求"""
    import re
    from answer_parser import parse_answers

    ctx = _load_context(student_id)
    last_ids = ctx.get("last_pushed_question_ids", [])
    if not last_ids:
        return {"reply": "找不到最近推送的题目，请先让我出题。", "push_result": None}

    # 尝试从消息中提取题号（如"第2题解析"、"2题"）
    raw_msg = intent.raw
    m = re.search(r"第\s*(\d+)\s*题|(\d+)\s*题", raw_msg)

    question_id = None
    if m:
        # 提取题号（从1开始）
        seq = int(m.group(1) or m.group(2))
        if 1 <= seq <= len(last_ids):
            question_id = last_ids[seq - 1]

    # 如果没有指定题号，默认显示最后一道题的解析
    if not question_id:
        question_id = last_ids[-1]

    return {
        "reply": "解析来了 👇",
        "push_result": None,
        "show_answer_for": question_id,
    }


def _handle_adjust_difficulty(student_id: str, intent: Intent) -> dict:
    import json
    from config import STUDENTS_DIR

    profile_path = os.path.join(STUDENTS_DIR, f"{student_id}.json")
    if not os.path.exists(profile_path):
        return {"reply": "还没有你的学习记录，先让我出题吧。", "push_result": None}

    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    current = profile.get("current_difficulty", 3)
    delta = intent.difficulty_delta or 0
    new_diff = max(1, min(5, current + delta))
    profile["current_difficulty"] = new_diff

    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    direction = "提升" if delta > 0 else "降低"
    return {
        "reply": f"好的，难度已{direction}到 {new_diff} 级，下次出题会按新难度推送。",
        "push_result": None,
    }


def _handle_show_progress(student_id: str, intent: Intent) -> dict:
    from report_generator import generate_report
    report = generate_report(student_id, period="week")
    return {"reply": report, "push_result": None}


def _handle_show_logs(student_id: str, intent: Intent) -> dict:
    """处理查看学习日志的请求"""
    from logger import query_student_history

    records = query_student_history(student_id, log_type="all")

    if not records:
        return {"reply": "还没有学习记录呢，快来做题吧！", "push_result": None}

    # 统计各类型事件
    push_count = sum(1 for r in records if r.get("event_type") == "push")
    answer_count = sum(1 for r in records if r.get("event_type") == "answer")
    grading_count = sum(1 for r in records if r.get("event_type") == "grading")
    correct_count = sum(1 for r in records if r.get("event_type") == "grading" and r.get("is_correct"))

    reply_lines = [
        "📊 你的学习日志统计：",
        f"📚 推送次数: {push_count}",
        f"✍️  答题次数: {answer_count}",
        f"📝 评判记录: {grading_count}",
        f"✅ 正确题数: {correct_count}/{grading_count}" if grading_count > 0 else "",
    ]

    reply = "\n".join(line for line in reply_lines if line)
    return {"reply": reply, "push_result": None}


# ── CLI 入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--save-context":
        # 用法: intent_router.py --save-context <student_id> <ids_json> <message> <bot_reply>
        if len(sys.argv) < 6:
            sys.exit(1)
        _, _, sid, ids_json, msg, reply = sys.argv[:6]
        save_context(sid, json.loads(ids_json), msg, reply)
        sys.exit(0)

    if len(sys.argv) < 3:
        print("用法: python intent_router.py <student_id> <message>")
        sys.exit(1)

    sid, msg = sys.argv[1], " ".join(sys.argv[2:])
    result = route(sid, msg)
    print(json.dumps(result, ensure_ascii=False, indent=2))
