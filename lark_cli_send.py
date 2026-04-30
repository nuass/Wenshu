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
import urllib.request
import urllib.error
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
            cwd=str(_BASE_DIR),
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
    p = Path(image_path)
    if p.is_absolute():
        try:
            image_path = str(p.relative_to(_BASE_DIR))
        except ValueError:
            pass
    return _run(["im", "+messages-send", "--chat-id", chat_id, "--image", image_path])


def send_card(chat_id: str, card: dict | str) -> dict:
    """
    发送飞书互动卡片。
    card 可以是 dict 或 JSON 字符串。
    返回 dict 中包含 message_id（来自 lark-cli 响应）。
    """
    if isinstance(card, dict):
        card_str = json.dumps(card, ensure_ascii=False)
    else:
        card_str = card
    result = _run([
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card_str,
    ])
    # lark-cli 返回格式：{"data": {"message_id": "om_xxx"}, "code": 0}
    if not result.get("message_id"):
        msg_id = result.get("data", {}).get("message_id", "")
        if msg_id:
            result["message_id"] = msg_id
    return result


def cardkit_create_card(card: dict) -> str:
    """
    用 cardkit API 创建卡片实体，返回 card_id。
    卡片实体支持 partial_update_element 局部变色。
    """
    token = _get_tenant_access_token()
    payload = json.dumps({"type": "card_json", "data": json.dumps(card, ensure_ascii=False)}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/cardkit/v1/cards",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        card_id = result.get("data", {}).get("card_id", "")
        if not card_id:
            raise RuntimeError(f"cardkit 创建卡片失败: {result}")
        return card_id
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"cardkit 创建卡片 HTTP 错误 {e.code}: {body}")


def send_card_entity(chat_id: str, card_id: str) -> dict:
    """
    将已创建的卡片实体发送到群，返回包含 message_id 的 dict。
    """
    token = _get_tenant_access_token()
    content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)
    payload = json.dumps({
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": content,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        msg_id = result.get("data", {}).get("message_id", "")
        return {"message_id": msg_id, **result}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"send_card_entity 失败 {e.code}: {body}")
        return {"ok": False, "error": body}


def send_text_to_user(open_id: str, text: str) -> dict:
    """发送私信（直接消息）给指定用户。"""
    return _run(["im", "+messages-send", "--user-id", open_id, "--text", text])


def update_card(message_id: str, card: dict | str) -> dict:
    """
    更新已发送的飞书互动卡片（全量替换，兼容旧逻辑保留）。
    """
    if isinstance(card, str):
        card = json.loads(card)

    body = json.dumps({"content": json.dumps(card, ensure_ascii=False), "msg_type": "interactive"}, ensure_ascii=False)
    return _run([
        "api", "PATCH",
        f"/open-apis/im/v1/messages/{message_id}",
        "--data", body,
    ], check=False)


import time as _time

# tenant_access_token 内存缓存（飞书 token 有效期 7200s，提前 5 分钟刷新）
_token_cache: dict = {"token": "", "expire_at": 0.0}


def _get_tenant_access_token() -> str:
    """
    用 second 账号（虾仁）的 app_id/app_secret 获取 tenant_access_token。
    结果缓存在内存中，距过期 5 分钟内才重新请求，避免每次调用都发网络请求。
    """
    now = _time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"]:
        return _token_cache["token"]

    from dotenv import load_dotenv
    load_dotenv(str(_BASE_DIR / "feishu" / ".env"))
    load_dotenv(str(_BASE_DIR / ".env"))

    app_id = os.environ.get("FEISHU_APP_ID_SECOND", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET_SECOND", "")
    if not app_id or not app_secret:
        raise RuntimeError("未找到 FEISHU_APP_ID_SECOND / FEISHU_APP_SECRET_SECOND 环境变量")

    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    token = data.get("tenant_access_token", "")
    if not token:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    expire = data.get("expire", 7200)
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + expire - 300  # 提前 5 分钟刷新
    log.debug(f"tenant_access_token 已刷新，有效期 {expire}s")
    return token


def cardkit_update_buttons(card_id: str, sequence: int,
                           selected_answer: str, is_correct: bool,
                           correct_answer: str,
                           options: list[str] | None = None) -> dict:
    """
    用 cardkit partial_update_element 局部更新按钮颜色。
    每个按钮的 element_id 格式为 btn_{option}_{card_id_suffix}，
    与 build_question_card 中保持一致。
    """
    if options is None:
        options = ["A", "B", "C", "D"]

    selected = selected_answer.upper()
    correct = correct_answer.upper() if correct_answer else ""

    actions = []
    for option in options:
        if option == selected and is_correct:
            btn_type = "primary"
            label = f"✅ {option}"
        elif option == selected and not is_correct:
            btn_type = "danger"
            label = f"❌ {option}"
        elif option == correct and not is_correct:
            btn_type = "primary"
            label = f"✅ {option}"
        else:
            btn_type = "default"
            label = option

        element_id = f"btn_{option}_{card_id[-10:]}"
        actions.append({
            "action": "partial_update_element",
            "params": {
                "element_id": element_id,
                "partial_element": {
                    "text": {"tag": "plain_text", "content": label},
                    "type": btn_type,
                },
            },
        })

    token = _get_tenant_access_token()
    payload = json.dumps({
        "sequence": sequence,
        "actions": actions,
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}/batch_update",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        log.debug(f"cardkit batch_update: {result}")
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"cardkit batch_update 失败 {e.code}: {body}")
        return {"code": e.code, "error": body}


def build_answered_card(img_key: str, question_id: int, seq: int,
                        selected_answer: str, is_correct: bool,
                        correct_answer: str,
                        options: list[str] | None = None) -> dict:
    """
    构造答题后显示结果的卡片：
    - 选中的按钮变色（正确=绿色 primary，错误=红色 danger）
    - 正确答案按钮始终显示为绿色（当答错时）
    - 其余按钮保持默认灰色，且所有按钮禁用
    """
    if options is None:
        options = ["A", "B", "C", "D"]

    selected = selected_answer.upper()
    correct = correct_answer.upper() if correct_answer else ""

    columns = []
    for option in options:
        if option == selected and is_correct:
            btn_type = "primary"   # 答对：绿色
            label = f"✅ {option}"
        elif option == selected and not is_correct:
            btn_type = "danger"    # 答错选的：红色
            label = f"❌ {option}"
        elif option == correct and not is_correct:
            btn_type = "primary"   # 答错时标出正确答案为绿色
            label = f"✅ {option}"
        else:
            btn_type = "default"
            label = option

        columns.append({
            "tag": "column",
            "elements": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": btn_type,
                "disabled": True,   # 已作答，禁止重复点击
                "value": {
                    "question_id": question_id,
                    "answer": option,
                },
            }],
        })

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
                    "tag": "column_set",
                    "flex_mode": "stretch",
                    "columns": columns,
                },
            ]
        },
    }


# ── 卡片构造（与 feishu_send.py 保持一致）─────────────────────

def build_question_card(image_path: str, question_id: int, seq: int,
                        options: list[str] | None = None,
                        message_id: str = "",
                        card_id: str = "",
                        teacher_id: str = "") -> dict:
    """
    构造带答题按钮的飞书互动卡片。

    Args:
        image_path:  本地图片路径或已上传的 image_key（img_xxx）
        question_id: 题目 ID，嵌入按钮 value，回调时直接取出
        seq:         本次推送中的题目序号（第几题）
        options:     答题选项列表，默认 ["A","B","C","D"]
        message_id:  已发送卡片的消息 ID，嵌入 value 供回调时取出
        card_id:     卡片实体 ID，嵌入 value 供 cardkit 局部更新变色用

    Returns:
        飞书卡片 JSON dict，可直接传给 send_card()
    """
    if options is None:
        options = ["A", "B", "C", "D"]

    img_key = _ensure_image_key(image_path)

    columns = []
    for option in options:
        btn_value: dict = {
            "question_id": question_id,
            "answer": option,
            "seq": seq,
            "img_key": img_key,
        }
        if message_id:
            btn_value["message_id"] = message_id
        if card_id:
            btn_value["card_id"] = card_id
        if teacher_id:
            btn_value["teacher_id"] = teacher_id
        columns.append({
            "tag": "column",
            "elements": [{
                "tag": "button",
                "element_id": f"btn_{option}_{card_id[-10:]}" if card_id else f"btn_{option}",
                "text": {"tag": "plain_text", "content": option},
                "type": "default",
                "value": btn_value,
                "behaviors": [{
                    "type": "callback",
                    "value": btn_value,
                }],
            }],
        })

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
                    "tag": "column_set",
                    "flex_mode": "stretch",
                    "columns": columns,
                },
            ]
        },
    }


# 图片 key 内存缓存（image_path → img_key），进程内永久有效（image_key 不过期）
_image_key_cache: dict[str, str] = {}


def _ensure_image_key(image_path: str) -> str:
    """
    如果传入的是本地路径，调用 lark-cli 上传并返回 image_key。
    如果已经是 img_xxx 格式，直接返回。
    上传结果缓存在内存中，同一路径不重复上传。
    """
    if str(image_path).startswith("img_"):
        return image_path

    if image_path in _image_key_cache:
        log.debug(f"图片 key 缓存命中: {image_path}")
        return _image_key_cache[image_path]

    # lark-cli ≥ 1.0.11 的 --file 只支持相对路径（相对于 _BASE_DIR / cwd）
    p = Path(image_path)
    if p.is_absolute():
        try:
            rel_path = str(p.relative_to(_BASE_DIR))
        except ValueError:
            # 路径不在 _BASE_DIR 下，使用原始路径（可能失败）
            rel_path = image_path
    else:
        rel_path = image_path

    result = _run([
        "im", "images", "create",
        "--data", json.dumps({"image_type": "message"}),
        "--file", rel_path,
    ], check=True)

    # 返回格式：{"data": {"image_key": "img_xxx"}, "code": 0}
    img_key = result.get("image_key") or result.get("data", {}).get("image_key")
    if not img_key:
        raise RuntimeError(f"图片上传失败，未获取到 image_key: {result}")
    return img_key
