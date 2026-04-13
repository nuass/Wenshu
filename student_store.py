#!/usr/bin/env python3
"""
student_store.py

学生画像 & 对话上下文的统一读写模块。
所有需要 load_student / save_student / load_context / save_context 的模块
都从这里 import，不再各自重复实现。

导出接口：
    load_student(student_id)               -> dict
    save_student(student)                  -> None
    load_context(student_id)               -> dict
    save_context(student_id, ...)          -> None
    load_questions(teacher_id)             -> dict[int, dict]
    load_roster()                          -> dict
    get_student_bindings(roster, open_id)  -> list[dict]
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import STUDENTS_DIR, QUESTIONS_JSON as _questions_json_path, questions_json

# 导出 questions_json 给外部模块使用
__all__ = ["questions_json"]

# ── 学生画像 ──────────────────────────────────────────────────

_DEFAULT_PROFILE_TEMPLATE = {
    "student_id":         "",
    "name":               "",
    "teacher_id":         "",
    "subject":            "",
    "current_difficulty": 3,
    "topic_mastery":      {},
    "send_history":       [],
    "weak_topics":        [],
}


def load_student(student_id: str, *, require_exists: bool = False) -> dict:
    """加载学生画像。

    Args:
        student_id: 学生 open_id。
        require_exists: True 时若文件不存在抛出 FileNotFoundError（record_answer 场景）；
                        False 时返回默认初始画像（push_engine 场景）。
    """
    p = Path(STUDENTS_DIR) / f"{student_id}.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    if require_exists:
        raise FileNotFoundError(
            f"学生画像不存在: {p}\n"
            "请先调用 push_engine.py 为该学生生成推送（自动创建画像）。"
        )
    profile = dict(_DEFAULT_PROFILE_TEMPLATE)
    profile["student_id"] = student_id
    profile["name"] = student_id
    return profile


def save_student(student: dict) -> None:
    """持久化学生画像。student 须含 'student_id' 字段。"""
    p = Path(STUDENTS_DIR) / f"{student['student_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(student, f, ensure_ascii=False, indent=2)


# ── 题库 ──────────────────────────────────────────────────────

_ROSTER_PATH = Path(STUDENTS_DIR) / "roster.json"


def load_roster() -> dict:
    """加载 roster.json，返回完整结构（含 teachers / students 两个顶级键）。"""
    if not _ROSTER_PATH.exists():
        return {"teachers": {}, "students": {}}
    with open(_ROSTER_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_student_bindings(roster: dict, open_id: str) -> list[dict]:
    """返回指定学生的所有 bindings 列表。

    每条 binding 格式：
        {"teacher_id": "chenxi", "chat_id": "oc_xxx", "subject": "AP统计"}

    若学生不在 roster 中，或 bindings 为空，返回空列表。
    """
    student_entry = roster.get("students", {}).get(open_id, {})
    return student_entry.get("bindings", [])


def get_binding_for_chat(roster: dict, open_id: str, chat_id: str) -> dict | None:
    """根据 chat_id 找到学生在该群对应的 binding（teacher_id + subject）。

    用于：收到飞书消息时，根据 sender open_id + chat_id 确定当前科目上下文。
    找不到时返回 None。
    """
    for binding in get_student_bindings(roster, open_id):
        if binding.get("chat_id") == chat_id:
            return binding
    return None

def load_questions(teacher_id: str = "") -> dict[int, dict]:
    """从 questions.json 返回 {id: question} 映射。

    Args:
        teacher_id: 指定老师 ID，自动定位对应题库路径。
                    为空时尝试读取环境变量 DEFAULT_TEACHER_ID，
                    否则抛出 ValueError。
    """
    if not teacher_id:
        teacher_id = os.environ.get("DEFAULT_TEACHER_ID", "")
    if not teacher_id:
        raise ValueError(
            "load_questions() 需要传入 teacher_id，"
            "或设置环境变量 DEFAULT_TEACHER_ID。"
        )
    p = _questions_json_path(teacher_id)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {q["id"]: q for q in json.load(f)}


# ── 对话上下文 ────────────────────────────────────────────────

def load_context(student_id: str) -> dict:
    """读取学生的对话上下文（last_pushed_question_ids / recent_messages 等）。"""
    path = Path(STUDENTS_DIR) / f"{student_id}_context.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_context(
    student_id: str,
    last_pushed_ids: list[int],
    message: str = "",
    bot_reply: str = "",
) -> None:
    """持久化推题上下文，追加最近对话记录（最多保留 10 条）。

    Args:
        student_id:       学生 open_id。
        last_pushed_ids:  本次推送的题目 ID 列表。
        message:          学生原始消息（可为空）。
        bot_reply:        Bot 回复内容（可为空）。
    """
    path = Path(STUDENTS_DIR) / f"{student_id}_context.json"
    ctx = load_context(student_id)

    ts = datetime.now().isoformat(timespec="seconds")
    recent: list[dict] = ctx.get("recent_messages", [])
    if message:
        recent.append({"role": "student", "content": message, "ts": ts})
    reply_text = bot_reply or f"推送了 {len(last_pushed_ids)} 道题"
    recent.append({"role": "bot", "content": reply_text, "ts": ts})

    ctx["student_id"] = student_id
    ctx["last_pushed_question_ids"] = last_pushed_ids
    ctx["recent_messages"] = recent[-10:]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)
