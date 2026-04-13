#!/usr/bin/env python3
"""
answer_parser.py

从飞书群消息中提取学生提交的答案，并匹配到最近推送的题目。

职责：
  1. 从原始消息文本中解析出选项字母（A/B/C/D）
     — 规则匹配优先，失败时调用 LLM 提取（支持口语化批量格式）
  2. 从学生对话上下文中取出最近推送的题目 id 列表
  3. 匹配"这条答案对应哪道题"（支持多题批量提交）
  4. 返回 [(question_id, answer)] 列表供 record_answer.py 消费

使用方式：
  # 作为模块
  from answer_parser import parse_answers

  # CLI 调试
  python answer_parser.py <student_id> "答案是A、B、D"
"""

import json
import os
import re
import sys
from typing import NamedTuple

from config import STUDENTS_DIR
from logger import log_answer_event
from student_store import load_context, load_student as _load_student_profile


# ── 数据结构 ──────────────────────────────────────────────────

class AnswerMatch(NamedTuple):
    question_id: int
    answer: str          # 大写字母 A/B/C/D/E


# ── LLM 答案提取 ──────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """\
你是一个答案提取助手。学生在飞书群里用各种方式提交多道选择题的答案。
你需要从消息中按题目顺序提取每道题的答案字母（A/B/C/D/E）。

规则：
- 按题目顺序返回，第1题答案在前
- 每道题只能有一个字母
- 如果某题答案不明确，跳过（不要猜）
- 只返回 JSON，格式：{"answers": ["A", "B", "C"]}
- 如果完全提取不到答案，返回：{"answers": []}

示例输入 → 输出：
"答案是A、B、D"          → {"answers": ["A", "B", "D"]}
"A B C"                  → {"answers": ["A", "B", "C"]}
"第一题A第二题B第三题C"   → {"answers": ["A", "B", "C"]}
"选A,B,C"               → {"answers": ["A", "B", "C"]}
"1A 2B 3C"              → {"answers": ["A", "B", "C"]}
"ABD"                    → {"answers": ["A", "B", "D"]}
"我选A吧，第二题B，最后一题感觉是D" → {"answers": ["A", "B", "D"]}
"""


def _extract_answers_via_llm(message: str) -> list[str]:
    """
    调用 LLM 从消息中提取答案字母列表（按题目顺序）。
    使用 requests 直接调用，返回大写字母列表，如 ["A", "B", "D"]；失败返回空列表。
    """
    import json as _json
    import re as _re
    import requests as _requests
    from config import UNIAPI_KEY, UNIAPI_BASE

    def _parse_content(content: str) -> list[str]:
        """从 LLM 返回文本中提取 answers 列表，兼容带推理过程的输出。"""
        # 找最后一个 {"answers": [...]} 块
        matches = list(_re.finditer(r'\{[^{}]*"answers"\s*:\s*\[[^\]]*\][^{}]*\}', content))
        if matches:
            data = _json.loads(matches[-1].group(0))
        else:
            data = _json.loads(content.strip())
        answers = data.get("answers", [])
        return [a.upper() for a in answers if str(a).upper() in "ABCDE" and len(str(a)) == 1]

    payload = {
        "model": "kimi-k2.5",
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        "max_tokens": 500,
        "temperature": 0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {UNIAPI_KEY}",
    }

    try:
        resp = _requests.post(
            f"{UNIAPI_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # kimi-k2.5：content 是最终答案，reasoning_content 是推理过程
        content = msg.get("content") or msg.get("reasoning_content") or ""
        return _parse_content(content)
    except Exception:
        return []


# ── 规则答案提取 ──────────────────────────────────────────────

def extract_raw_answers(message: str) -> list[tuple[int | None, str]]:
    """
    从消息中提取 (题号或None, 答案字母) 列表。
    题号从 1 开始（对应推送顺序，不是 question_id）。

    优先级：
    1. 带题号的规则匹配（第1题A、1.A、①A）
    2. 连续答案规则匹配（答案是A、B、D）
    3. LLM 提取（处理各种口语化格式）
    4. 单字母匹配（兜底）
    """
    message = message.strip()
    results: list[tuple[int | None, str]] = []
    seen_positions: set[int] = set()

    # ── 1. 带题号的规则匹配 ───────────────────────────────────
    numbered_pattern = re.compile(
        r"(?:第\s*(\d+)\s*题|(\d+)[.)、]\s*|([①②③④⑤⑥⑦⑧⑨⑩]))\s*[选答是：:为]?\s*([ABCDE])",
        re.IGNORECASE,
    )
    for m in numbered_pattern.finditer(message):
        pos = m.start()
        if pos in seen_positions:
            continue
        seen_positions.add(pos)

        num_str = m.group(1) or m.group(2)
        circle = m.group(3)
        answer = m.group(4).upper()

        if num_str:
            seq = int(num_str)
        elif circle:
            circle_map = "①②③④⑤⑥⑦⑧⑨⑩"
            seq = circle_map.index(circle) + 1
        else:
            seq = None

        results.append((seq, answer))

    if results:
        return results

    # ── 2. 连续答案规则匹配（答案是A、B、C 或 A B C 或 ABD）────
    # 格式：答案是A、B、D / 选A,B,C / A B C
    continuous_pattern = re.compile(
        r"(?:答案|选项|答)?(?:是|为|：|:)?\s*([ABCDE])(?:\s*[、,，\s]\s*([ABCDE])){1,4}",
        re.IGNORECASE,
    )
    m = continuous_pattern.search(message)
    if m:
        letters = re.findall(r"[ABCDE]", m.group(0), re.IGNORECASE)
        if len(letters) >= 2:
            for letter in letters:
                results.append((None, letter.upper()))
            return results

    # 连续字母串（如 "ABD" "ABC"）
    compact_pattern = re.compile(r"^[ABCDE]{2,5}$", re.IGNORECASE)
    if compact_pattern.match(message.strip()):
        for letter in message.strip().upper():
            results.append((None, letter))
        return results

    # ── 3. LLM 提取（口语化批量格式兜底）────────────────────────
    # 判断消息是否像在提交答案（包含选项字母），再调 LLM
    if re.search(r"[ABCDE]", message, re.IGNORECASE):
        llm_answers = _extract_answers_via_llm(message)
        if llm_answers:
            for letter in llm_answers:
                results.append((None, letter))
            return results

    # ── 4. 单字母兜底 ─────────────────────────────────────────
    stripped = message.strip().upper()
    if stripped in ("A", "B", "C", "D", "E"):
        results.append((None, stripped))

    return results


# ── 题目匹配 ──────────────────────────────────────────────────

def _fallback_ids_from_profile(student_id: str) -> list[int]:
    """
    上下文文件缺失时，从学生画像 send_history 恢复最近推送的题目 ID。
    取 send_history 末尾相同 sent_at 日期的连续记录，按推送顺序返回。
    """
    try:
        profile = _load_student_profile(student_id)
    except Exception:
        return []
    history = profile.get("send_history", [])
    if not history:
        return []
    latest_date = history[-1].get("sent_at", "")
    if not latest_date:
        return []
    ids: list[int] = []
    for record in reversed(history):
        if record.get("sent_at") != latest_date:
            break
        ids.insert(0, record["question_id"])
    return ids


def parse_answers(student_id: str, message: str, chat_id: str = None) -> list[AnswerMatch]:
    """
    主入口：解析消息，返回 [(question_id, answer)] 列表。

    匹配逻辑：
    - 若消息含题号（第1题、1.、①），按顺序映射到 last_pushed_question_ids
    - 若无题号，按提取顺序依次匹配推送列表
    - 推送列表不足时，多余答案丢弃
    """
    raw = extract_raw_answers(message)
    if not raw:
        return []

    ctx = load_context(student_id)
    pushed_ids: list[int] = ctx.get("last_pushed_question_ids", [])

    # 上下文文件缺失时，从 send_history 恢复最近推送批次
    if not pushed_ids:
        pushed_ids = _fallback_ids_from_profile(student_id)

    if not pushed_ids:
        return []

    matches: list[AnswerMatch] = []

    for seq, answer in raw:
        if seq is not None:
            # 题号从 1 开始，映射到推送列表索引
            idx = seq - 1
            if 0 <= idx < len(pushed_ids):
                matches.append(AnswerMatch(question_id=pushed_ids[idx], answer=answer))
        else:
            # 无题号：按顺序消费推送列表
            idx = len(matches)
            if idx < len(pushed_ids):
                matches.append(AnswerMatch(question_id=pushed_ids[idx], answer=answer))

    # 去重（同一题只保留第一次出现）
    seen: set[int] = set()
    deduped: list[AnswerMatch] = []
    for m in matches:
        if m.question_id not in seen:
            seen.add(m.question_id)
            deduped.append(m)

    # 记录答题事件
    if deduped:
        parsed_answers_dict = {m.question_id: m.answer for m in deduped}
        log_answer_event(
            student_id=student_id,
            raw_text=message,
            parsed_answers=parsed_answers_dict,
            chat_id=chat_id,
        )

    return deduped


def parse_card_answer(student_id: str, question_id: int, answer: str, chat_id: str = None) -> AnswerMatch:
    """
    卡片按钮回调专用：question_id 和 answer 已由卡片 value 直接提供，
    无需文字解析，直接记录日志并返回 AnswerMatch。
    """
    answer = answer.upper()
    log_answer_event(
        student_id=student_id,
        raw_text=f"[card] Q{question_id}={answer}",
        parsed_answers={question_id: answer},
        chat_id=chat_id,
    )
    return AnswerMatch(question_id=question_id, answer=answer)


# ── CLI 调试 ──────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python answer_parser.py <student_id> <message>")
        sys.exit(1)

    sid = sys.argv[1]
    msg = " ".join(sys.argv[2:])

    results = parse_answers(sid, msg)
    if not results:
        print("未能解析出答案（检查消息格式或学生上下文是否存在）")
    else:
        for r in results:
            print(f"  题目 {r.question_id} → 答案 {r.answer}")
