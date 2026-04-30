#!/usr/bin/env python3
"""
slash_command.py

学生快捷命令处理模块。

拦截以 "/" 开头的消息，直接路由到对应功能，跳过 AI 意图识别，
响应更快且行为完全可预期。

支持的命令
──────────
/帮助  /help                         显示帮助
/出题  [知识点] [难度1-5]            推题（可选按知识点/难度过滤）
/复习  [错题 | <知识点>]             推送错题或按知识点复习
/进度  [week | month | all]          查看学习进度报告
/记忆                                查看学生学习记忆摘要
/难度  <1-5>                         设置当前难度
/任务  [开始 <序号> | 进度]          查看作业列表或开始作业
"""

from __future__ import annotations

import re
from typing import Optional

# ── 帮助文本 ──────────────────────────────────────────────────────────────────

_HELP_TEXT = """\
📖 可用快捷命令：

/出题 [知识点] [难度]
  立刻推题，可选按知识点或难度过滤
  例：/出题  /出题 假设检验  /出题 箱线图 3

/复习 [错题 | 知识点]
  推送我的错题或指定知识点题目复习
  例：/复习  /复习 错题  /复习 假设检验

/进度 [week | month | all]
  查看学习进度报告（默认本周）
  例：/进度  /进度 month

/记忆
  查看系统记录的我的学习画像摘要

/难度 <1-5>
  设置推题难度（1最简单，5最难）
  例：/难度 3  /难度 4

/任务 [开始 <序号>]
  查看我的作业列表，或开始指定序号的作业
  例：/任务  /任务 开始 1

/帮助  /help
  显示此帮助信息
"""

# ── 命令解析 ──────────────────────────────────────────────────────────────────

# 命令别名映射 → 标准命令名
_ALIASES: dict[str, str] = {
    "帮助": "帮助", "help": "帮助", "h": "帮助",
    "出题": "出题", "推题": "出题", "做题": "出题",
    "复习": "复习", "review": "复习",
    "进度": "进度", "报告": "进度", "progress": "进度",
    "记忆": "记忆", "memory": "记忆",
    "难度": "难度", "difficulty": "难度",
    "任务": "任务", "作业": "任务", "task": "任务",
}


def parse_slash(message: str) -> Optional[tuple[str, list[str]]]:
    """
    解析斜杠命令消息。

    Returns:
        (command, args) 元组；若不是斜杠命令则返回 None。
        command 是标准命令名（如"出题"），args 是剩余参数列表。
    """
    stripped = message.strip()
    if not stripped.startswith("/"):
        return None

    # 去掉前导 /，按空白分词
    parts = stripped[1:].split()
    if not parts:
        return None

    raw_cmd = parts[0].lower() if parts[0].isascii() else parts[0]
    # 中文命令不 lower
    cmd_key = parts[0]
    if cmd_key.lower() in _ALIASES:
        cmd_key = cmd_key.lower()
    command = _ALIASES.get(cmd_key)
    if not command:
        return None

    return command, parts[1:]


# ── 各命令处理函数 ─────────────────────────────────────────────────────────────

def _cmd_help(student_id: str, args: list[str], chat_id: str) -> dict:
    return {"reply": _HELP_TEXT, "push_result": None}


def _cmd_push(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /出题 [知识点] [难度]
    解析参数：最后一个纯数字 1-5 视为难度，其余合并为知识点关键词。
    """
    import push_engine

    difficulty: int | None = None
    topic_keyword: str | None = None

    # 找难度（1-5 的纯数字）
    remaining = []
    for arg in args:
        if re.fullmatch(r"[1-5]", arg):
            difficulty = int(arg)
        else:
            remaining.append(arg)

    if remaining:
        topic_keyword = " ".join(remaining)

    # 如果有过滤条件用 push_manual，否则用普通 push
    if difficulty is not None or topic_keyword is not None:
        result = push_engine.push_manual(
            student_id,
            chapter=topic_keyword,
            difficulty=difficulty,
            chat_id=chat_id,
        )
        if not result.get("questions"):
            hint = []
            if topic_keyword:
                hint.append(f'知识点"{topic_keyword}"')
            if difficulty is not None:
                hint.append(f"难度{difficulty}")
            filter_desc = "、".join(hint)
            return {
                "reply": f"没有找到符合条件的题目（{filter_desc}），换个条件试试？",
                "push_result": None,
            }
    else:
        result = push_engine.push(student_id, chat_id=chat_id)
        if not result.get("questions"):
            return {
                "reply": result.get("message", "今日题目已发送，明日再来~"),
                "push_result": None,
            }

    return {"reply": None, "push_result": result}


def _cmd_review(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /复习 [错题 | 知识点]
    - 无参数或"错题"：从 send_history 找错题重新推送
    - 有知识点：按知识点过滤推题
    """
    import push_engine
    from student_store import load_student

    keyword = " ".join(args).strip() if args else ""
    is_wrong = not keyword or keyword in ("错题", "错误", "wrong")

    if is_wrong:
        # 从 send_history 中找曾经答错的题
        student = load_student(student_id)
        wrong_ids = _collect_wrong_ids(student)

        if not wrong_ids:
            return {"reply": "暂时没有错题记录，继续加油做题吧！", "push_result": None}

        result = push_engine.push_manual(
            student_id,
            question_ids=wrong_ids,
            chat_id=chat_id,
        )
        if not result.get("questions"):
            return {"reply": "错题复习题目加载失败，请稍后再试。", "push_result": None}

        count = len(result["questions"])
        return {
            "reply": f"找到 {len(wrong_ids)} 道错题，为你推送其中 {count} 道，加油复习！",
            "push_result": result,
        }
    else:
        # 按知识点过滤
        result = push_engine.push_manual(
            student_id,
            chapter=keyword,
            chat_id=chat_id,
        )
        if not result.get("questions"):
            return {
                "reply": f'没有找到"{keyword}"相关题目，换个关键词试试？',
                "push_result": None,
            }
        return {"reply": f'为你推送"{keyword}"相关题目，加油复习！', "push_result": result}


def _collect_wrong_ids(student: dict) -> list[int]:
    """从 send_history 中收集答错的题目 ID。"""
    history = student.get("send_history", [])
    wrong: list[int] = []
    seen: set[int] = set()
    for entry in reversed(history):  # 最近的优先
        for ans in entry.get("answers", []):
            qid = ans.get("question_id")
            if qid and not ans.get("is_correct") and qid not in seen:
                wrong.append(qid)
                seen.add(qid)
    return wrong


def _cmd_progress(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /进度 [week | month | all]
    """
    from report_generator import generate_report

    period_map = {
        "week": "week", "周": "week", "本周": "week",
        "month": "month", "月": "month", "本月": "month",
        "all": "all", "全部": "all", "全": "all",
    }
    period_arg = args[0] if args else "week"
    period = period_map.get(period_arg, "week")

    report = generate_report(student_id, period=period)
    return {"reply": report, "push_result": None}


def _cmd_memory(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /记忆  — 展示学生的学习记忆摘要
    """
    try:
        from memory_store import get_memory_summary
        summary = get_memory_summary(student_id)
    except Exception:
        summary = ""

    if not summary:
        return {
            "reply": "还没有积累到足够的学习记忆，继续做题后会自动生成哦！",
            "push_result": None,
        }

    return {"reply": f"📚 你的学习记忆摘要：\n\n{summary}", "push_result": None}


def _cmd_difficulty(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /难度 <1-5>  — 设置学生当前难度
    """
    import json
    import os
    from config import STUDENTS_DIR

    if not args or not re.fullmatch(r"[1-5]", args[0]):
        return {
            "reply": "请输入 1-5 的数字设置难度，例如：/难度 3",
            "push_result": None,
        }

    new_diff = int(args[0])
    profile_path = os.path.join(STUDENTS_DIR, f"{student_id}.json")

    if not os.path.exists(profile_path):
        return {"reply": "还没有你的学习记录，先让我出题吧。", "push_result": None}

    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    old_diff = profile.get("current_difficulty", 3)
    profile["current_difficulty"] = new_diff

    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    direction = "提升" if new_diff > old_diff else ("降低" if new_diff < old_diff else "保持")
    return {
        "reply": f"✅ 难度已{direction}到 {new_diff} 级（原来是 {old_diff} 级），下次出题按新难度推送。",
        "push_result": None,
    }


def _cmd_task(student_id: str, args: list[str], chat_id: str) -> dict:
    """
    /任务           — 列出所有作业及进度
    /任务 开始 <N>  — 开始第 N 个未完成的作业（触发推题）
    """
    from task_store import get_student_assignments, get_pending_assignments
    import push_engine

    # 解析子命令
    sub = args[0].strip() if args else ""
    is_start = sub in ("开始", "start", "做", "开始做")

    if is_start:
        # /任务 开始 <序号>
        pending = get_pending_assignments(student_id)
        if not pending:
            return {"reply": "你目前没有未完成的作业，继续保持！", "push_result": None}

        # 解析序号
        idx_str = args[1] if len(args) > 1 else "1"
        try:
            idx = int(idx_str) - 1
        except ValueError:
            idx = 0
        if idx < 0 or idx >= len(pending):
            return {
                "reply": f"序号 {idx_str} 超出范围，共有 {len(pending)} 个未完成作业。",
                "push_result": None,
            }

        target = pending[idx]
        question_ids = target.get("question_ids") or []
        chapter = target.get("chapter") or None
        difficulty = target.get("difficulty") or None

        if question_ids:
            result = push_engine.push_manual(
                student_id, question_ids=question_ids, chat_id=chat_id,
            )
        elif chapter or difficulty:
            result = push_engine.push_manual(
                student_id, chapter=chapter, difficulty=difficulty, chat_id=chat_id,
            )
        else:
            result = push_engine.push(student_id, chat_id=chat_id)

        if not result.get("questions"):
            return {
                "reply": f"作业「{target['name']}」题目加载失败，请联系老师。",
                "push_result": None,
            }

        return {
            "reply": f"开始作业「{target['name']}」，截止 {target['due_date']}，加油！",
            "push_result": result,
        }

    else:
        # /任务 — 列出所有作业
        all_assignments = get_student_assignments(student_id)
        if not all_assignments:
            return {"reply": "目前没有布置作业，认真做题就好！", "push_result": None}

        STATUS_LABEL = {
            "pending":     "未开始",
            "in_progress": "进行中",
            "completed":   "已完成",
            "overdue":     "已过期",
        }
        STATUS_ICON = {
            "pending": "📋", "in_progress": "✏️", "completed": "✅", "overdue": "⌛",
        }

        lines = ["📚 你的作业列表：", ""]
        pending_idx = 1
        for a in all_assignments:
            status = a["status"]
            icon = STATUS_ICON.get(status, "📋")
            label = STATUS_LABEL.get(status, status)
            progress_str = ""
            if a["total"] > 0:
                progress_str = f" {a['progress']}/{a['total']} 题"

            if status in ("pending", "in_progress"):
                lines.append(f"{pending_idx}. {icon} 【{label}】{a['name']}{progress_str}")
                lines.append(f"   截止：{a['due_date']}  → 发送 /任务 开始 {pending_idx}")
                pending_idx += 1
            else:
                lines.append(f"   {icon} 【{label}】{a['name']}{progress_str}")

        return {"reply": "\n".join(lines), "push_result": None}


# ── 命令分发表 ────────────────────────────────────────────────────────────────

_HANDLERS = {
    "帮助": _cmd_help,
    "出题": _cmd_push,
    "复习": _cmd_review,
    "进度": _cmd_progress,
    "记忆": _cmd_memory,
    "难度": _cmd_difficulty,
    "任务": _cmd_task,
}


# ── 主入口 ────────────────────────────────────────────────────────────────────

def handle_slash(student_id: str, message: str, chat_id: str = "") -> dict | None:
    """
    尝试处理斜杠命令。

    Returns:
        与 intent_router.route() 格式相同的结果字典；
        若 message 不是斜杠命令则返回 None（交由意图路由处理）。
    """
    parsed = parse_slash(message)
    if parsed is None:
        # 以 "/" 开头但未匹配到任何命令，给友好提示
        if message.startswith("/"):
            cmd_token = message.split()[0] if message.split() else message
            return {
                "reply": f'未知命令"{cmd_token}"，发送 /帮助 查看可用命令。',
                "push_result": None,
            }
        return None

    command, args = parsed
    handler = _HANDLERS.get(command)
    if handler is None:
        # 已 "/" 开头但命令未知，给提示
        return {
            "reply": f'未知命令"/{command}"，发送 /帮助 查看可用命令。',
            "push_result": None,
        }

    try:
        return handler(student_id, args, chat_id)
    except Exception as e:
        return {
            "reply": f"命令执行出错：{e}，请稍后再试。",
            "push_result": None,
        }


# ── CLI 测试入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("用法: python slash_command.py <student_id> <message>")
        print('例如: python slash_command.py stu_001 "/帮助"')
        sys.exit(1)

    sid = sys.argv[1]
    msg = " ".join(sys.argv[2:])
    result = handle_slash(sid, msg)
    if result is None:
        print(f"[不是斜杠命令] {msg!r}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
