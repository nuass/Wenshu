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
import time
from pathlib import Path

# sequence 用当前毫秒时间戳，跨进程/重启后仍单调递增，不会低于推题时的值
def _next_seq() -> int:
    return int(time.time() * 1000) % 2_000_000_000

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


def _question_options(q: dict) -> list[str]:
    """从题目 options 字段推断选项列表，无法推断时默认 ABCD。"""
    opts = q.get("options")
    if isinstance(opts, dict) and opts:
        return sorted(opts.keys())
    return ["A", "B", "C", "D"]


def _cardkit_put(card_id: str, card: dict, sequence: int) -> None:
    """全量更新 cardkit 卡片实体内容（用于回注 card_id 后的首次更新）。"""
    import urllib.request, urllib.error
    token_data = lcs._get_tenant_access_token()
    payload = json.dumps({
        "card": {"type": "card_json", "data": json.dumps(card, ensure_ascii=False)},
        "sequence": sequence,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}",
        data=payload,
        headers={
            "Authorization": f"Bearer {token_data}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get("code", 0) != 0:
            print(f"[feishu_bot] cardkit PUT 警告: {result}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[feishu_bot] cardkit PUT 失败 {e.code}: {body}", file=sys.stderr)


# ── 老师模式 ──────────────────────────────────────────────────

def handle_teacher(target_open_id: str, chat_id: str) -> dict:
    """
    老师模式：给 target_open_id 对应的学生推送题目。
    1. 调用 push_engine.push() 选题（题数由 roster 中 teacher.push_count 决定）
    2. 每道题用 lark-cli 发送带答题按钮的互动卡片（选项由题目 options 字段决定）
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

    teacher_id = result.get("teacher_id", "")

    import concurrent.futures
    sent_ids: list[int] = []

    def _prepare_one(seq_q):
        seq, q = seq_q
        img = q.get("question_image")
        if not img:
            return None
        abs_path = _abs_image_path(img)
        opts = _question_options(q)
        card_draft = lcs.build_question_card(abs_path, q["question_id"], seq, options=opts, teacher_id=teacher_id)
        card_id = lcs.cardkit_create_card(card_draft)
        card_with_id = lcs.build_question_card(
            abs_path, q["question_id"], seq,
            options=opts, card_id=card_id, teacher_id=teacher_id,
        )
        _cardkit_put(card_id, card_with_id, sequence=_next_seq())
        return (seq, q, card_id)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        prepared = list(pool.map(_prepare_one, enumerate(questions, 1)))

    # 按原始顺序发送
    for item in prepared:
        if item is None:
            continue
        seq, q, card_id = item
        try:
            send_result = lcs.send_card_entity(chat_id, card_id)
            msg_id = send_result.get("message_id", "")
            sent_ids.append(q["question_id"])
            print(f"[feishu_bot] 卡片发送成功 card_id={card_id} msg_id={msg_id}", file=sys.stderr)
        except Exception as e:
            print(f"[feishu_bot] 卡片发送失败 card_id={card_id}: {e}", file=sys.stderr)

    if sent_ids:
        save_context(student_id, sent_ids, "teacher push", f"推送了 {len(sent_ids)} 道题")

    return {"ok": True, "sent_count": len(sent_ids)}


# ── 卡片回调模式 ──────────────────────────────────────────────

def handle_card_answer(sender_open_id: str, chat_id: str,
                       question_id: int, answer: str,
                       card_id: str = "", teacher_id: str = "",
                       seq: int = 0, skip_card_update: bool = False,
                       send_feedback: bool = True) -> dict:
    """
    卡片按钮回调模式：question_id 和 answer 由卡片直接提供，无需文字解析。
    1. 调用 parse_card_answer 记录日志
    2. 查题目正确答案，判断对错
    3. 调用 record_answer.record 更新学生画像
    4. 用 cardkit partial_update_element 将按钮变色（正确绿/错误红）
    5. 发送文字反馈 + 解析图
    """
    from answer_parser import parse_card_answer
    import record_answer as ra

    student_id = _resolve_student_id(sender_open_id)
    match = parse_card_answer(student_id, question_id, answer, chat_id=chat_id)

    # 取正确答案
    from push_engine import load_questions_for_teacher, load_questions
    if teacher_id:
        qs = load_questions_for_teacher(teacher_id)
    else:
        qs = load_questions()
    questions_db = {q.get("question_id", q.get("id")): q for q in qs}
    q = questions_db.get(question_id, {})
    correct_answer = q.get("correct_answer", "")
    is_correct = match.answer == correct_answer.upper() if correct_answer else None
    print(f"[feishu_bot] handle_card_answer: qid={question_id} ans={match.answer!r} correct={correct_answer!r} is_correct={is_correct} seq={seq} teacher_id={teacher_id!r}", file=sys.stderr)

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
    seq_prefix = f"第{seq}题 " if seq is not None and seq > 0 else ""
    if is_correct is None:
        feedback = seq_prefix + _render("no_answer", "✅ 已收到你的答案：{student_answer}（题目暂无标准答案）")
    elif is_correct:
        feedback = seq_prefix + _render("correct", "✅ 正确！答案是 {correct_answer}，继续加油！")
    else:
        feedback = seq_prefix + _render("wrong", "❌ 答案是 {correct_answer}，你选了 {student_answer}，来看看解析吧👇")

    # ── 卡片变色：用 cardkit PUT 全量更新卡片实体内容 ──
    card_put_ok = False
    if card_id and not skip_card_update:
        try:
            _opts = _question_options(q)
            img_key = q.get("question_image", "")
            if img_key and not img_key.startswith("img_"):
                img_key = lcs._ensure_image_key(_abs_image_path(img_key))
            answered = lcs.build_answered_card(
                img_key=img_key,
                question_id=question_id,
                seq=seq,
                selected_answer=match.answer,
                is_correct=bool(is_correct),
                correct_answer=correct_answer or "",
                options=_opts,
            )
            _cardkit_put(card_id, answered, sequence=_next_seq())
            card_put_ok = True
        except Exception as e:
            print(f"[feishu_bot] 卡片变色失败: {e}", file=sys.stderr)

    if send_feedback and card_put_ok:
        lcs.send_text(chat_id, feedback)

        # 答错时发解析文字 + 解析图
        if is_correct is False:
            answer_text = q.get("answer_text", "")
            if answer_text:
                try:
                    lcs.send_text(chat_id, answer_text)
                except Exception as e:
                    print(f"[feishu_bot] 解析文字发送失败: {e}", file=sys.stderr)
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
        p_teacher_id = push_result.get("teacher_id", "")

        sent_ids: list[int] = []

        def _prepare_one_s(seq_q):
            seq, q = seq_q
            img = q.get("question_image")
            if not img:
                return None
            abs_path = _abs_image_path(img)
            opts = _question_options(q)
            card_draft = lcs.build_question_card(abs_path, q["question_id"], seq, options=opts, teacher_id=p_teacher_id)
            card_id = lcs.cardkit_create_card(card_draft)
            card_with_id = lcs.build_question_card(
                abs_path, q["question_id"], seq,
                options=opts, card_id=card_id, teacher_id=p_teacher_id,
            )
            _cardkit_put(card_id, card_with_id, sequence=_next_seq())
            return (seq, q, card_id)
        with _cf.ThreadPoolExecutor(max_workers=5) as pool:
            prepared_s = list(pool.map(_prepare_one_s, enumerate(push_result["questions"], start=1)))

        for item in prepared_s:
            if item is None:
                continue
            seq, q, card_id = item
            try:
                lcs.send_card_entity(chat_id, card_id)
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
    parser.add_argument("--card-id",      dest="card_id", default="",
                        help="[card-answer] cardkit 卡片实体 ID，用于答题后变色")
    parser.add_argument("--seq",          dest="seq", type=int, default=0,
                        help="[card-answer] 题目序号（第几题）")
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
                                    args.question_id, args.answer,
                                    card_id=args.card_id or "",
                                    seq=args.seq)
    else:
        if not args.sender_id:
            parser.error("--mode student 需要 --sender-id 参数")
        result = handle_student(args.sender_id, args.chat_id, args.message)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
