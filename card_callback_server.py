#!/usr/bin/env python3
"""
card_callback_server.py

用飞书官方 lark-oapi SDK 的 WebSocket 长连接模式监听卡片回调。
收到 card.action.trigger 后调用 feishu_bot.handle_card_answer 处理答题并变色。

启动方式：
    cd /project/Wenshu-main
    python3 card_callback_server.py

保持后台运行：
    nohup python3 card_callback_server.py >> logs/card_callback.log 2>&1 &
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

_BASE = Path(__file__).parent
load_dotenv(str(_BASE / "feishu" / ".env"))
load_dotenv(str(_BASE / ".env"))

import lark_oapi as lark
from lark_oapi.ws import Client as WsClient
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
try:
    from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackCard
except ImportError:
    CallBackCard = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("card_callback")


def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """
    card.action.trigger 回调处理。
    立即返回变色卡片（避免飞书 3s 超时报错），耗时操作在后台线程执行。
    """
    event = data.event
    if not event:
        log.warning("回调 event 为空")
        return P2CardActionTriggerResponse()

    value = event.action.value if event.action else {}
    sender_open_id = event.operator.open_id if event.operator else ""
    chat_id = event.context.open_chat_id if event.context else ""
    question_id = value.get("question_id") if value else None
    answer = value.get("answer", "") if value else ""
    card_id = value.get("card_id", "") if value else ""
    img_key = value.get("img_key", "") if value else ""
    seq = value.get("seq", 1) if value else 1
    teacher_id = value.get("teacher_id", "") if value else ""

    log.info(
        f"卡片回调: sender={sender_open_id} chat={chat_id} "
        f"qid={question_id} ans={answer} card_id={card_id} teacher_id={teacher_id} seq={seq!r} img_key={img_key!r}"
    )

    if not sender_open_id or not question_id or not answer:
        log.warning("回调缺少必要字段，忽略")
        return P2CardActionTriggerResponse()

    answer_upper = str(answer).upper()
    qid = int(question_id)

    # ── 快速查题目正确答案（本地文件读取，毫秒级）──────────────────
    correct_answer = ""
    q_options: list[str] = []
    try:
        import lark_cli_send as lcs
        from push_engine import load_questions_for_teacher, load_questions
        if teacher_id:
            qs = load_questions_for_teacher(teacher_id)
        else:
            qs = load_questions()
        questions_db = {q.get("question_id", q.get("id")): q for q in qs}
        q_obj = questions_db.get(qid, {})
        correct_answer = q_obj.get("correct_answer", "")
        opts = q_obj.get("options", {})
        if isinstance(opts, dict) and opts:
            q_options = sorted(opts.keys())
    except Exception as e:
        log.error(f"查题目失败: {e}")

    is_correct = (answer_upper == correct_answer.upper()) if correct_answer else None

    # img_key 有值时，后台线程负责 PUT 变色 + 判断是否发文字
    # img_key 为空时，无法变色，静默处理
    card_updated_by_response = False  # 不再用 resp.card，改用后台 PUT

    # ── 后台线程：PUT 变色，成功后再发文字反馈 ────────────────────
    def _background():
        try:
            import feishu_bot as fb
            # skip_card_update=False：后台负责 PUT 变色
            # send_feedback 由后台根据 PUT 结果决定
            fb.handle_card_answer(
                sender_open_id=sender_open_id,
                chat_id=chat_id,
                question_id=qid,
                answer=answer_upper,
                card_id=card_id,
                teacher_id=teacher_id,
                seq=seq,
                skip_card_update=False,
                send_feedback=bool(img_key and card_id),  # 有 img_key+card_id 才尝试变色+反馈
            )
        except Exception as e:
            log.error(f"handle_card_answer 后台异常: {e}", exc_info=True)

    threading.Thread(target=_background, daemon=True).start()

    # ── 立即返回变色卡片 + toast，飞书客户端直接渲染，不等后台完成 ──
    resp = P2CardActionTriggerResponse()

    # toast：必须返回非空 content，否则飞书会报 200672 错误
    from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackToast
    toast = CallBackToast()
    seq_prefix = f"第{seq}题：" if seq else ""
    if is_correct is True:
        toast.type = "success"
        toast.content = f"{seq_prefix}回答正确！"
    elif is_correct is False:
        toast.type = "error"
        toast.content = f"{seq_prefix}回答错误"
    else:
        toast.type = "info"
        toast.content = f"{seq_prefix}已收到你的答案"
    resp.toast = toast

    return resp


def main():
    app_id = os.environ.get("FEISHU_APP_ID_SECOND", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET_SECOND", "")
    if not app_id or not app_secret:
        log.error("未找到 FEISHU_APP_ID_SECOND / FEISHU_APP_SECRET_SECOND，请检查 .env")
        sys.exit(1)

    log.info(f"启动卡片回调长连接，app_id={app_id}")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )

    cli = WsClient(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=handler,
    )
    cli.start()


if __name__ == "__main__":
    main()
