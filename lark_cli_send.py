#!/usr/bin/env python3
"""
lark_cli_send.py

通过 lark-cli 发送飞书消息，替代 feishu_send.py 中的 requests 直接调用。
lark-cli 自动处理 token 获取、图片上传、重试等，无需手动管理凭证。

对外接口（与 feishu_send.py 保持兼容）：
    send_text(chat_id, text)
    send_image(chat_id, image_path)          # 直接传本地路径，lark-cli 自动上传
    send_card(chat_id, card_json)            # 传卡片 dict 或 JSON 字符串
    build_question_card(image_key, question_id, seq)  # 复用原逻辑
"""

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# ── lark-cli 路径 ─────────────────────────────────────────────

def _find_lark_cli() -> str:
    candidate = os.environ.get("LARK_CLI_BIN")
    if candidate and Path(candidate).exists():
        return candidate
    for p in [
        "/root/.nvm/versions/node/v22.22.2/bin/lark-cli",
        "/usr/local/bin/lark-cli",
        "/usr/bin/lark-cli",
    ]:
        if Path(p).exists():
            return p
    return "lark-cli"  # fallback，依赖 PATH

LARK_CLI_BIN = _find_lark_cli()

# lark-cli 配置目录：存放 app_id/app_secret 的 JSON 凭证
# 默认读 feishu/ 目录，lark-cli 会自动识别 cli_*.json
_BASE_DIR = Path(__file__).parent
_FEISHU_DIR = _BASE_DIR / "feishu"


# ── 内部执行函数 ──────────────────────────────────────────────

def _run(args: list[str], check: bool = True) -> dict:
    """
    执行 lark-cli 命令，返回解析后的 JSON 结果。
    失败时记录日志，check=True 则抛出异常。
    """
    cmd = [LARK_CLI_BIN] + args + ["--as", "bot"]
    log.debug(f"lark-cli: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "LARK_CLI_CONFIG_DIR": str(_FEISHU_DIR)},
        )
        stdout = proc.stdout.strip()
        if proc.returncode != 0:
            err = proc.stderr.strip() or stdout
            log.error(f"lark-cli 失败 (exit {proc.returncode}): {err}")
            if check:
                raise RuntimeError(f"lark-cli 失败: {err}")
            return {"ok": False, "error": err}

        if not stdout:
            return {"ok": True}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # lark-cli 某些命令输出非 JSON（如上传返回 image_key 行）
            return {"ok": True, "raw": stdout}

    except subprocess.TimeoutExpired:
        log.error("lark-cli 超时")
        if check:
            raise
        return {"ok": False, "error": "timeout"}


# ── 发送接口 ──────────────────────────────────────────────────

def send_text(chat_id: str, text: str) -> dict:
    """发送纯文本消息。"""
    return _run(["im", "+messages-send", "--chat-id", chat_id, "--text", text])


def send_image(chat_id: str, image_path: str) -> dict:
    """
    发送图片。
    传本地文件路径时 lark-cli 自动上传再发送，无需手动调上传接口。
    """
    return _run(["im", "+messages-send", "--chat-id", chat_id, "--image", image_path])


def send_card(chat_id: str, card: dict | str) -> dict:
    """
    发送飞书互动卡片。
    card 可以是 dict 或 JSON 字符串。
    """
    if isinstance(card, dict):
        card_str = json.dumps(card, ensure_ascii=False)
    else:
        card_str = card
    return _run([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card_str,
    ])


def send_text_to_user(open_id: str, text: str) -> dict:
    """发送私信（直接消息）给指定用户。"""
    return _run(["im", "+messages-send", "--user-id", open_id, "--text", text])


# ── 卡片构造（与 feishu_send.py 保持一致）─────────────────────

def build_question_card(image_path: str, question_id: int, seq: int,
                        options: list[str] | None = None) -> dict:
    """
    构造带答题按钮的飞书互动卡片。

    Args:
        image_path:  本地图片路径或已上传的 image_key（img_xxx）
        question_id: 题目 ID，嵌入按钮 value，回调时直接取出
        seq:         本次推送中的题目序号（第几题）
        options:     答题选项列表，默认 ["A","B","C","D"]
                     可配置为 ["A","B","C","D","E"]（5选1）或 ["T","F"]（判断题）

    Returns:
        飞书卡片 JSON dict，可直接传给 send_card()
    """
    if options is None:
        options = ["A", "B", "C", "D"]

    buttons = []
    for option in options:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": option},
            "type": "default",
            "value": {
                "question_id": question_id,
                "answer": option,
            },
        })

    img_key = _ensure_image_key(image_path)

    return {
        "schema": "2.0",
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**第 {seq} 题**",
                },
                {
                    "tag": "img",
                    "img_key": img_key,
                    "alt": {"tag": "plain_text", "content": f"第{seq}题题目图片"},
                    "mode": "fit_horizontal",
                },
                {
                    "tag": "action",
                    "actions": buttons,
                },
            ]
        },
    }


def _ensure_image_key(image_path: str) -> str:
    """
    如果传入的是本地路径，调用 lark-cli 上传并返回 image_key。
    如果已经是 img_xxx 格式，直接返回。
    """
    if str(image_path).startswith("img_"):
        return image_path

    # lark-cli 使用位置参数而非 --file flag
    result = _run([
        "im", "images", "create",
        "--data", json.dumps({"image_type": "message"}),
        image_path,  # 位置参数
    ], check=True)

    # lark-cli 上传图片返回格式：{"image_key": "img_xxx"}
    img_key = result.get("image_key") or result.get("data", {}).get("image_key")
    if not img_key:
        raise RuntimeError(f"图片上传失败，未获取到 image_key: {result}")
    return img_key
