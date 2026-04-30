#!/usr/bin/env python3
"""
task_store.py

Wenshu 飞书推题系统 — 作业任务管理层

老师在管理后台创建作业，系统为每个绑定学生生成作业记录并发送飞书通知；
学生通过 /任务 命令查看和开始作业；作业完成或到期时更新状态。

数据文件：
    feishu/assignments.json

数据结构：
    {
      "assignments": [
        {
          "id":          "uuid",
          "name":        "五一假期练习",
          "teacher_id":  "chenxi",
          "description": "第5-7章综合练习",
          "question_ids": [101, 102, 103],   // 指定题目（优先）
          "chapter":     "第5章",             // 或按章节过滤（question_ids 为空时用）
          "difficulty":  3,                   // 可选难度过滤
          "due_date":    "2026-05-05",        // 截止日期
          "created_at":  "2026-04-30",
          "student_assignments": {
            "<student_id>": {
              "status":       "pending",      // pending|in_progress|completed|overdue
              "chat_id":      "oc_xxx",
              "total":        15,             // 总题数
              "progress":     0,              // 已答题数
              "notified_at":  "2026-04-30",  // 首次通知时间
              "reminded_at":  null,           // 最后提醒时间
              "completed_at": null
            }
          }
        }
      ]
    }

导出接口：
    create_assignment(...)  -> dict
    get_assignment(id)      -> dict | None
    list_assignments(teacher_id) -> list[dict]
    delete_assignment(id)   -> bool
    update_student_progress(assignment_id, student_id, answered_count) -> bool
    mark_overdue()          -> list[str]   # 返回变为 overdue 的 assignment_id
    get_student_assignments(student_id) -> list[dict]  # 该学生所有作业
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

# ── 路径配置 ──────────────────────────────────────────────────────────────────

_BASE = Path(__file__).parent
_DATA_FILE = _BASE / "feishu" / "assignments.json"


# ── 文件读写 ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not _DATA_FILE.exists():
        return {"assignments": []}
    with open(_DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 作业 CRUD ────────────────────────────────────────────────────────────────

def create_assignment(
    name: str,
    teacher_id: str,
    student_bindings: list[dict],   # [{"student_id": ..., "chat_id": ...}, ...]
    due_date: str,                   # "YYYY-MM-DD"
    description: str = "",
    question_ids: list[int] | None = None,
    chapter: str | None = None,
    difficulty: int | None = None,
) -> dict:
    """
    创建作业并为每个学生初始化作业记录。
    返回创建好的作业 dict。
    """
    # 计算总题数
    total = len(question_ids) if question_ids else 0

    student_assignments: dict[str, dict] = {}
    today = str(date.today())
    for b in student_bindings:
        sid = b["student_id"]
        student_assignments[sid] = {
            "status": "pending",
            "chat_id": b.get("chat_id", ""),
            "total": total,
            "progress": 0,
            "notified_at": None,
            "reminded_at": None,
            "completed_at": None,
        }

    assignment = {
        "id": str(uuid.uuid4()),
        "name": name,
        "teacher_id": teacher_id,
        "description": description,
        "question_ids": question_ids or [],
        "chapter": chapter or "",
        "difficulty": difficulty,
        "due_date": due_date,
        "created_at": today,
        "student_assignments": student_assignments,
    }

    data = _load()
    data["assignments"].append(assignment)
    _save(data)
    return assignment


def get_assignment(assignment_id: str) -> dict | None:
    """按 ID 查找作业。"""
    data = _load()
    return next((a for a in data["assignments"] if a["id"] == assignment_id), None)


def list_assignments(teacher_id: str | None = None) -> list[dict]:
    """列出所有作业，可按 teacher_id 过滤。"""
    data = _load()
    assignments = data["assignments"]
    if teacher_id:
        assignments = [a for a in assignments if a.get("teacher_id") == teacher_id]
    return assignments


def delete_assignment(assignment_id: str) -> bool:
    """删除作业，返回是否成功。"""
    data = _load()
    before = len(data["assignments"])
    data["assignments"] = [a for a in data["assignments"] if a["id"] != assignment_id]
    if len(data["assignments"]) == before:
        return False
    _save(data)
    return True


# ── 学生进度 ──────────────────────────────────────────────────────────────────

def update_student_progress(
    assignment_id: str,
    student_id: str,
    progress: int,
) -> bool:
    """
    更新学生在某作业中的答题进度。
    progress 达到 total 时自动标记为 completed。
    返回是否成功。
    """
    data = _load()
    for assignment in data["assignments"]:
        if assignment["id"] != assignment_id:
            continue
        sa = assignment["student_assignments"].get(student_id)
        if sa is None:
            return False
        sa["progress"] = progress
        total = sa.get("total", 0)
        if total > 0 and progress >= total:
            sa["status"] = "completed"
            sa["completed_at"] = str(date.today())
        elif sa["status"] == "pending":
            sa["status"] = "in_progress"
        _save(data)
        return True
    return False


def mark_student_notified(assignment_id: str, student_id: str) -> None:
    """记录已向学生发送通知的时间。"""
    data = _load()
    for assignment in data["assignments"]:
        if assignment["id"] != assignment_id:
            continue
        sa = assignment["student_assignments"].get(student_id)
        if sa is not None:
            sa["notified_at"] = str(date.today())
            _save(data)
            return


def mark_student_reminded(assignment_id: str, student_id: str) -> None:
    """记录最后一次提醒时间。"""
    data = _load()
    for assignment in data["assignments"]:
        if assignment["id"] != assignment_id:
            continue
        sa = assignment["student_assignments"].get(student_id)
        if sa is not None:
            sa["reminded_at"] = str(date.today())
            _save(data)
            return


def mark_overdue() -> list[str]:
    """
    将所有已过截止日期但未完成的学生作业标记为 overdue。
    返回受影响的 assignment_id 列表（去重）。
    """
    today = date.today()
    data = _load()
    affected: set[str] = set()

    for assignment in data["assignments"]:
        due = assignment.get("due_date", "")
        if not due:
            continue
        try:
            due_date = date.fromisoformat(due)
        except ValueError:
            continue
        if due_date >= today:
            continue
        for sid, sa in assignment["student_assignments"].items():
            if sa["status"] in ("pending", "in_progress"):
                sa["status"] = "overdue"
                affected.add(assignment["id"])

    if affected:
        _save(data)
    return list(affected)


# ── 学生视角查询 ──────────────────────────────────────────────────────────────

def get_student_assignments(student_id: str) -> list[dict]:
    """
    获取某学生的所有作业（含作业元信息 + 该学生的进度）。
    按截止日期升序排序，pending/in_progress 在前。
    """
    data = _load()
    result = []
    for assignment in data["assignments"]:
        sa = assignment["student_assignments"].get(student_id)
        if sa is None:
            continue
        result.append({
            "id":          assignment["id"],
            "name":        assignment["name"],
            "description": assignment.get("description", ""),
            "due_date":    assignment.get("due_date", ""),
            "created_at":  assignment.get("created_at", ""),
            "question_ids": assignment.get("question_ids", []),
            "chapter":     assignment.get("chapter", ""),
            "difficulty":  assignment.get("difficulty"),
            "teacher_id":  assignment.get("teacher_id", ""),
            # 学生进度字段
            "status":       sa["status"],
            "chat_id":      sa.get("chat_id", ""),
            "total":        sa.get("total", 0),
            "progress":     sa.get("progress", 0),
            "notified_at":  sa.get("notified_at"),
            "completed_at": sa.get("completed_at"),
        })

    # 排序：未完成优先，然后按截止日期升序
    STATUS_ORDER = {"pending": 0, "in_progress": 1, "overdue": 2, "completed": 3}
    result.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9), x["due_date"]))
    return result


def get_pending_assignments(student_id: str) -> list[dict]:
    """返回学生尚未完成的作业（pending + in_progress）。"""
    return [a for a in get_student_assignments(student_id)
            if a["status"] in ("pending", "in_progress")]


# ── 作业统计（供后台展示）────────────────────────────────────────────────────

def assignment_summary(assignment: dict) -> dict:
    """计算作业的完成统计。"""
    sa_map = assignment.get("student_assignments", {})
    total = len(sa_map)
    completed = sum(1 for sa in sa_map.values() if sa["status"] == "completed")
    in_progress = sum(1 for sa in sa_map.values() if sa["status"] == "in_progress")
    overdue = sum(1 for sa in sa_map.values() if sa["status"] == "overdue")
    pending = sum(1 for sa in sa_map.values() if sa["status"] == "pending")
    return {
        "total_students": total,
        "completed": completed,
        "in_progress": in_progress,
        "overdue": overdue,
        "pending": pending,
    }


# ── CLI 测试 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        assignments = list_assignments()
        print(f"共 {len(assignments)} 个作业")
        for a in assignments:
            summ = assignment_summary(a)
            print(f"  [{a['id'][:8]}] {a['name']} 截止:{a['due_date']} "
                  f"完成:{summ['completed']}/{summ['total_students']}")

    elif cmd == "overdue":
        affected = mark_overdue()
        print(f"标记过期: {affected}")

    elif cmd == "student" and len(sys.argv) > 2:
        sid = sys.argv[2]
        assignments = get_student_assignments(sid)
        print(f"{sid} 的作业（共 {len(assignments)} 个）：")
        for a in assignments:
            bar = f"{a['progress']}/{a['total']}" if a['total'] else "未指定题数"
            print(f"  [{a['status']}] {a['name']} 截止:{a['due_date']} 进度:{bar}")

    else:
        print("用法:")
        print("  python task_store.py list")
        print("  python task_store.py student <student_id>")
        print("  python task_store.py overdue")
