#!/usr/bin/env python3
"""
report_generator.py

读取学生画像，生成周报/月报文字，调用 Claude API 润色后返回。

使用方式：
  # 作为模块
  from report_generator import generate_report

  # CLI 调试
  python report_generator.py <student_id> [--period week|month]
"""

import json
import os
import sys
from datetime import datetime, timedelta

from openai import OpenAI
from config import UNIAPI_KEY, UNIAPI_BASE
from student_store import load_student as _load_student

_client = OpenAI(api_key=UNIAPI_KEY, base_url=UNIAPI_BASE)

_SYSTEM_PROMPT = """\
你是一位专业的 AP 统计家教老师，正在为学生生成学习报告。

要求：
- 用中文，语气鼓励但客观
- 结构清晰，使用数据说话
- 对薄弱点给出具体的下一步建议（具体到知识点，不要泛泛而谈）
- 控制在 200 字以内
- 不要使用 markdown，纯文本即可
"""


# ── 数据加载 ──────────────────────────────────────────────────

def _filter_by_period(history: list[dict], days: int) -> list[dict]:
    """只保留最近 days 天内有答题记录的条目。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [
        h for h in history
        if h.get("answered") and h.get("answered_at", h.get("sent_at", "")) >= cutoff
    ]


# ── 统计计算 ──────────────────────────────────────────────────

def _compute_stats(history: list[dict], mastery: dict[str, float]) -> dict:
    total = len(history)
    correct = sum(1 for h in history if h.get("is_correct"))
    accuracy = correct / total if total else 0.0

    # 按知识点分组计算本期正确率
    topic_correct: dict[str, int] = {}
    topic_total: dict[str, int] = {}
    for h in history:
        for tag in h.get("topic_tags", []):
            topic_total[tag] = topic_total.get(tag, 0) + 1
            if h.get("is_correct"):
                topic_correct[tag] = topic_correct.get(tag, 0) + 1

    topic_accuracy: dict[str, float] = {
        tag: topic_correct.get(tag, 0) / cnt
        for tag, cnt in topic_total.items()
    }

    # 进步最大的知识点（本期正确率 - 历史掌握度）
    improvements = {
        tag: topic_accuracy[tag] - mastery.get(tag, topic_accuracy[tag])
        for tag in topic_accuracy
    }
    best_topic = max(improvements, key=improvements.get) if improvements else None
    best_delta = improvements.get(best_topic, 0) if best_topic else 0

    # 薄弱点（本期正确率 < 0.6）
    weak = [tag for tag, acc in topic_accuracy.items() if acc < 0.6]

    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "topic_accuracy": topic_accuracy,
        "best_topic": best_topic,
        "best_delta": best_delta,
        "weak_topics": weak,
    }


# ── 报告生成 ──────────────────────────────────────────────────

def _build_raw_report(
    student_name: str,
    period_label: str,
    stats: dict,
    difficulty: int,
) -> str:
    """构造结构化原始报告，供 Claude 润色。"""
    lines = [
        f"学生：{student_name}",
        f"周期：{period_label}",
        f"练习题目：{stats['total']} 道",
        f"正确率：{stats['accuracy']*100:.0f}%",
        f"当前难度：{difficulty} 级",
    ]

    if stats["topic_accuracy"]:
        lines.append("\n知识点本期正确率：")
        for tag, acc in sorted(stats["topic_accuracy"].items(), key=lambda x: x[1]):
            flag = "⚠️" if acc < 0.6 else "✅"
            lines.append(f"  {flag} {tag}：{acc*100:.0f}%")

    if stats["best_topic"] and stats["best_delta"] > 0.05:
        lines.append(f"\n进步最大：{stats['best_topic']}（+{stats['best_delta']*100:.0f}%）")

    if stats["weak_topics"]:
        lines.append(f"\n需要加强：{'、'.join(stats['weak_topics'])}")

    return "\n".join(lines)


def generate_report(student_id: str, period: str = "week") -> str:
    """
    生成学习报告。
    period: "week"（近7天）或 "month"（近30天）
    返回可直接发送的报告文字。
    """
    profile = _load_student(student_id)
    if not profile:
        return "暂无学习记录，先让我出题吧~"

    days = 7 if period == "week" else 30
    period_label = _period_label(days)

    history = _filter_by_period(profile.get("send_history", []), days)
    mastery = profile.get("topic_mastery", {})
    difficulty = profile.get("current_difficulty", 3)
    name = profile.get("name", student_id)

    if not history:
        return f"{period_label}暂无答题记录，坚持每天练习效果更好哦~"

    stats = _compute_stats(history, mastery)
    raw = _build_raw_report(name, period_label, stats, difficulty)

    try:
        resp = _client.chat.completions.create(
            model="claude-sonnet-4-5",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"请根据以下数据生成学习报告：\n\n{raw}"},
            ],
            max_tokens=300,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        # 降级：直接返回结构化原始报告
        return raw


def _period_label(days: int) -> str:
    end = datetime.now()
    start = end - timedelta(days=days)
    return f"{start.strftime('%m/%d')}–{end.strftime('%m/%d')}"


# ── CLI 调试 ──────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python report_generator.py <student_id> [--period week|month]")
        sys.exit(1)

    sid = sys.argv[1]
    period = "week"
    if "--period" in sys.argv:
        idx = sys.argv.index("--period")
        if idx + 1 < len(sys.argv):
            period = sys.argv[idx + 1]

    print(generate_report(sid, period))
