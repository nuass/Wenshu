#!/usr/bin/env python3
"""
feishu_bot.py

飞书机器人消息处理入口。
消息发送全部通过 lark_cli_send（lark-cli）完成，无需手动管理 token。

支持三种模式：
  teacher     — 老师模式：给指定学生推送题目卡片
  student     — 学生模式：处理学生消息（意图路由 → 答题/出题/进度等）
  card-answer — 卡片按钮回调：精准匹配 question_id + answer，无需文字解析

用法：
    python feishu_bot.py --mode teacher \\
        --target-id ou_ef54461cdd57b75bd4b174e04032205d \\
        --chat-id  oc_4003a1795f471dc00f6b5c90fc43da8d

    python feishu_bot.py --mode student \\
        --sender-id ou_ef54461cdd57b75bd4b174e04032205d \\
        --chat-id   oc_4003a1795f471dc00f6b5c90fc43da8d \\
        --message   "选A"

    python feishu_bot.py --mode card-answer \\
        --sender-id ou_ef54461cdd57b75bd4b174e04032205d \\
        --chat-id   oc_4003a1795f471dc00f6b5c90fc43da8d \\
        --question-id 42 --answer A

输出（stdout）：JSON
    {"ok": true, "sent_count": 3}
    {"ok": false, "error": "..."}
"""

import argparse
import json
import re
import sys
from pathlib import Path

import lark_cli_send as lcs
from student_store import save_context

# ── 配置 ──────────────────────────────────────────────────────

# 飞书 open_id → student_id 映射（直接用 open_id 作 student_id 时留空）
STUDENT_ID_MAP: dict[str, str] = {
    "ou_3633edbc174dc91ed028f27b29ffb51e": "stu_001",
}

BASE_DIR = Path(__file__).parent


def _resolve_student_id(open_id: str) -> str:
    return STUDENT_ID_MAP.get(open_id, open_id)


def _abs_image_path(image_path: str) -> str:
    """将相对路径转换为绝对路径（相对于项目根目录）。"""
    p = Path(image_path)
    return str(p) if p.is_absolute() else str(BASE_DIR / p)


def _get_teacher_options(teacher_id: str, roster: dict) -> list[str]:
    """从 roster 读取老师配置的答题选项，默认 ABCD。"""
    return roster.get("teachers", {}).get(teacher_id, {}).get(
        "answer_options", ["A", "B", "C", "D"]
    )


# ── 老师模式 ──────────────────────────────────────────────────

def handle_teacher(target_open_id: str, chat_id: str) -> dict:
    """
    老师模式：给 target_open_id 对应的学生推送题目。
    1. 调用 push_engine.push() 选题（题数由 roster 中 teacher.push_count 决定）
    2. 每道题用 lark-cli 发送带答题按钮的互动卡片（选项由 teacher.answer_options 决定）
    3. 发送文字确认消息
    4. 持久化推题上下文
    """
    import push_engine
    from student_store import load_roster

    student_id = _resolve_student_id(target_open_id)
    print(f"[feishu_bot] teacher mode: student_id={student_id}", file=sys.stderr)

    result = push_engine.push(student_id, chat_id=chat_id)
    questions = result.get("questions", [])

    if not questions:
        msg = result.get("message", "暂无可推送题目。")
        lcs.send_text(chat_id, msg)
        return {"ok": True, "sent_count": 0, "message": msg}

    roster = load_roster()
    teacher_id = result.get("teacher_id", "")
    answer_options = _get_teacher_options(teacher_id, roster)

    sent_ids: list[int] = []
    for seq, q in enumerate(questions, start=1):
        img = q.get("question_image")
        if not img:
            continue
        abs_path = _abs_image_path(img)
        try:
            card = lcs.build_question_card(abs_path, q["question_id"], seq, options=answer_options)
            lcs.send_card(chat_id, card)
            sent_ids.append(q["question_id"])
        except Exception as e:
            print(f"[feishu_bot] 卡片发送失败 {abs_path}: {e}", file=sys.stderr)

    if sent_ids:
        lcs.send_text(chat_id, f"已向学生发送 {len(sent_ids)} 道题目，请点击按钮作答。")
        save_context(student_id, sent_ids, "teacher push", f"推送了 {len(sent_ids)} 道题")

    return {"ok": True, "sent_count": len(sent_ids)}


# ── 卡片回调模式 ──────────────────────────────────────────────

def handle_card_answer(sender_open_id: str, chat_id: str,
                       question_id: int, answer: str) -> dict:
    """
    卡片按钮回调模式：question_id 和 answer 由卡片直接提供，无需文字解析。
    1. 调用 parse_card_answer 记录日志
    2. 查题目正确答案，判断对错
    3. 调用 record_answer.record 更新学生画像
    4. 用 lark-cli 发送反馈（正确/错误 + 解析图）
    """
    from answer_parser import parse_card_answer
    import record_answer as ra

    student_id = _resolve_student_id(sender_open_id)
    match = parse_card_answer(student_id, question_id, answer, chat_id=chat_id)

    # 取正确答案
    from push_engine import load_questions
    questions_db = {q["question_id"]: q for q in load_questions()}
    q = questions_db.get(question_id, {})
    correct_answer = q.get("correct_answer", "")
    is_correct = match.answer == correct_answer.upper() if correct_answer else None

    result = ra.record(
        student_id=student_id,
        question_id=question_id,
        submitted_answer=match.answer,
        is_correct=bool(is_correct),
        chat_id=chat_id,
    )

    # 读取老师消息模板
    from student_store import load_roster as _lr
    _roster = _lr()
    _student_entry = _roster.get("students", {}).get(student_id, {})
    _teacher_id = ""
    for b in _student_entry.get("bindings", []):
        if not chat_id or b.get("chat_id") == chat_id:
            _teacher_id = b.get("teacher_id", "")
            break
    _tpl = _roster.get("teachers", {}).get(_teacher_id, {}).get("message_templates", {})

    def _render(key: str, default: str) -> str:
        tmpl = _tpl.get(key, default)
        return tmpl.replace("{correct_answer}", correct_answer or "") \
                   .replace("{student_answer}", match.answer) \
                   .replace("{student_name}", _student_entry.get("name", student_id))

    # 反馈文字
    if is_correct is None:
        feedback = _render("no_answer", "✅ 已收到你的答案：{student_answer}（题目暂无标准答案）")
    elif is_correct:
        feedback = _render("correct", "✅ 正确！答案是 {correct_answer}，继续加油！")
    else:
        feedback = _render("wrong", "❌ 答案是 {correct_answer}，你选了 {student_answer}，来看看解析吧👇")

    lcs.send_text(chat_id, feedback)

    # 答错时用 lark-cli 发解析图（自动上传）
    if is_correct is False:
        answer_img = q.get("answer_image")
        if answer_img:
            abs_path = _abs_image_path(answer_img)
            try:
                lcs.send_image(chat_id, abs_path)
            except Exception as e:
                print(f"[feishu_bot] 解析图发送失败: {e}", file=sys.stderr)

    return {"ok": True, "is_correct": is_correct, **result}


# ── 学生模式 ──────────────────────────────────────────────────

def handle_student(sender_open_id: str, chat_id: str, message: str) -> dict:
    """
    学生模式：处理学生消息。
    1. 调用 intent_router.route() 识别意图
    2. 用 lark-cli 发送文字回复
    3. 推题时发卡片（带 ABCD 按钮）
    4. 发解析图（答题错误时）
    """
    import intent_router

    student_id = _resolve_student_id(sender_open_id)

    # 剥离飞书 <at> 标签
    clean_message = re.sub(r"<[^>]+>", "", message).strip()
    clean_message = re.sub(r"\s+", " ", clean_message).strip()

    print(f"[feishu_bot] student mode: student_id={student_id}, msg={clean_message!r}",
          file=sys.stderr)

    try:
        router_result = intent_router.route(student_id, clean_message, chat_id=chat_id)
    except Exception as e:
        lcs.send_text(chat_id, "处理消息时出错，请稍后再试。")
        return {"ok": False, "error": str(e)}

    print(f"[feishu_bot] intent result: {json.dumps(router_result, ensure_ascii=False)}",
          file=sys.stderr)

    # 1. 文字回复
    if router_result.get("reply"):
        lcs.send_text(chat_id, router_result["reply"])

    # 2. 推题：发卡片（含答题按钮，选项由老师配置决定）
    push_result = router_result.get("push_result")
    if push_result and push_result.get("questions"):
        from student_store import load_roster
        roster = load_roster()
        p_teacher_id = push_result.get("teacher_id", "")
        answer_options = _get_teacher_options(p_teacher_id, roster)

        sent_ids: list[int] = []
        for seq, q in enumerate(push_result["questions"], start=1):
            img = q.get("question_image")
            if not img:
                continue
            abs_path = _abs_image_path(img)
            try:
                card = lcs.build_question_card(abs_path, q["question_id"], seq, options=answer_options)
                lcs.send_card(chat_id, card)
                sent_ids.append(q["question_id"])
            except Exception as e:
                print(f"[feishu_bot] 推题卡片失败: {e}", file=sys.stderr)
        if sent_ids:
            save_context(student_id, sent_ids, message, router_result.get("reply") or "推送题目")

    # 3. 解析图
    for img_path in (router_result.get("answer_images") or []):
        try:
            lcs.send_image(chat_id, _abs_image_path(img_path))
        except Exception as e:
            print(f"[feishu_bot] 解析图发送失败: {e}", file=sys.stderr)

    return {"ok": True}


# ── CLI 入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="飞书机器人消息处理入口（消息发送由 lark-cli 完成）")
    parser.add_argument("--mode", required=True,
                        choices=["teacher", "student", "card-answer"],
                        help="处理模式")
    parser.add_argument("--chat-id",     required=True, dest="chat_id",
                        help="目标群/会话 chat_id")
    parser.add_argument("--target-id",   dest="target_id",
                        help="[teacher] 目标学生的 open_id")
    parser.add_argument("--sender-id",   dest="sender_id",
                        help="[student / card-answer] 发消息学生的 open_id")
    parser.add_argument("--question-id", dest="question_id", type=int,
                        help="[card-answer] 题目 ID（整数）")
    parser.add_argument("--answer",      default="",
                        help="[card-answer] 学生选择的答案字母（A/B/C/D）")
    parser.add_argument("--message",     default="",
                        help="[student] 学生原始消息内容（含飞书标签）")
    args = parser.parse_args()

    if args.mode == "teacher":
        if not args.target_id:
            parser.error("--mode teacher 需要 --target-id 参数")
        result = handle_teacher(args.target_id, args.chat_id)

    elif args.mode == "card-answer":
        if not args.sender_id or not args.question_id or not args.answer:
            parser.error("--mode card-answer 需要 --sender-id、--question-id、--answer 参数")
        result = handle_card_answer(args.sender_id, args.chat_id,
                                    args.question_id, args.answer)
    else:
        if not args.sender_id:
            parser.error("--mode student 需要 --sender-id 参数")
        result = handle_student(args.sender_id, args.chat_id, args.message)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
