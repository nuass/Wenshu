#!/usr/bin/env python3
"""
admin_server.py

轻量 Flask 管理后台，供家教老师查看和管理所有学生数据。

功能：
  - GET  /                        — 管理界面（单页 HTML）
  - GET  /api/students            — 所有学生列表
  - GET  /api/students/<id>       — 单个学生详情
  - POST /api/students            — 新增学生
  - POST /api/students/<id>       — 更新学生画像（难度/薄弱点/姓名）
  - DELETE /api/students/<id>     — 删除学生
  - GET  /api/teachers            — 老师列表
  - POST /api/teachers            — 新增老师
  - PUT  /api/teachers/<id>       — 编辑老师
  - DELETE /api/teachers/<id>     — 删除老师
  - GET  /api/chats               — 飞书群概览
  - GET  /api/roster              — 完整 roster.json
  - GET  /api/questions           — 题库概览（所有老师）
  - POST /api/push/<id>           — 手动为学生触发推题
  - GET  /api/report/<id>         — 生成学生周报
  - GET  /api/config              — 读取全局运行时配置
  - POST /api/config              — 更新全局运行时配置（dedup_days 等）
  - GET  /api/cron/push-interval  — 读取推题 cron 间隔（分钟）
  - POST /api/cron/push-interval  — 更新推题 cron 间隔
  - GET  /api/cron                — cron 任务列表
  - POST /api/cron                — 新建 cron 任务
  - PUT  /api/cron/<id>           — 更新 cron 任务（student_ids/schedule/name）
  - DELETE /api/cron/<id>         — 删除 cron 任务
  - POST /api/cron/<id>/toggle    — 启用/禁用 cron 任务

运行：
  python admin_server.py
  # 默认 http://localhost:5001
"""

import json
import os
import subprocess
import threading
import tempfile
import uuid
import secrets

from flask import Flask, jsonify, request, Response, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from config import STUDENTS_DIR, BASE_DIR
from student_store import (
    load_student as _store_load_student,
    save_student as _store_save_student,
    load_roster,
)
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SECRET_KEY") or secrets.token_hex(32)

PYTHON3 = os.getenv("PYTHON3_BIN", "/opt/anaconda3/bin/python3")
AUTO_SEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROSTER_PATH = Path(STUDENTS_DIR) / "roster.json"
CRON_PATH = Path(AUTO_SEND_DIR) / "feishu" / "cron_jobs.json"
APP_CONFIG_PATH = Path(AUTO_SEND_DIR) / "feishu" / "app_config.json"
USERS_PATH = Path(AUTO_SEND_DIR) / "feishu" / "users.json"

# 系统 crontab 中推题任务的标识注释
_CRON_PUSH_MARKER = "feishu_bot.py --mode teacher"


# ── 工具函数 ──────────────────────────────────────────────────

def save_roster(roster: dict) -> None:
    ROSTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(roster, ensure_ascii=False, indent=2)
    tmp = ROSTER_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(ROSTER_PATH)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── 用户认证 ──────────────────────────────────────────────────

def load_users() -> dict:
    if not USERS_PATH.exists():
        return {}
    with open(USERS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_users(users: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _init_default_users() -> None:
    """首次启动时创建默认 admin 账号。"""
    if USERS_PATH.exists():
        return
    users = {
        "admin": {
            "password_hash": generate_password_hash("admin123"),
            "role": "admin",
        }
    }
    # 从 roster.json 自动为每位老师创建账号（默认密码 teacher123）
    try:
        roster = load_roster()
        for tid in roster.get("teachers", {}):
            users[tid] = {
                "password_hash": generate_password_hash("teacher123"),
                "role": "teacher",
                "teacher_id": tid,
            }
    except Exception:
        pass
    save_users(users)
    print(f"[admin] 已创建默认账号：admin/admin123，各老师账号密码 teacher123", flush=True)


def current_user() -> dict:
    return session.get("user", {})


def current_teacher_id() -> str | None:
    u = current_user()
    return u.get("teacher_id") if u.get("role") == "teacher" else None


def is_admin() -> bool:
    return current_user().get("role") == "admin"


def _403():
    return jsonify({"error": "无权限"}), 403


# ── 认证中间件 ────────────────────────────────────────────────

_PUBLIC_ENDPOINTS = {"login_page", "login_submit", "static"}


@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not session.get("user"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "未登录"}), 401
        return redirect("/login")


# ── 登录 / 登出 ───────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 推题管理</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 flex items-center justify-center min-h-screen">
<div class="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm">
  <h1 class="text-xl font-semibold text-center mb-6 text-indigo-600">推题管理后台</h1>
  <form method="POST" action="/login" class="space-y-4">
    <div>
      <label class="block text-xs text-gray-500 mb-1">用户名</label>
      <input name="username" autofocus required
        class="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300">
    </div>
    <div>
      <label class="block text-xs text-gray-500 mb-1">密码</label>
      <input name="password" type="password" required
        class="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300">
    </div>
    {error_block}
    <button type="submit"
      class="w-full bg-indigo-600 text-white py-2 rounded-lg text-sm hover:bg-indigo-700">登录</button>
  </form>
</div>
</body>
</html>"""


@app.get("/login")
def login_page():
    return Response(_LOGIN_HTML.replace("{error_block}", ""), mimetype="text/html")


@app.post("/login")
def login_submit():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    users = load_users()
    user = users.get(username)
    if not user or not check_password_hash(user["password_hash"], password):
        err = '<p class="text-red-500 text-xs text-center">用户名或密码错误</p>'
        return Response(_LOGIN_HTML.replace("{error_block}", err), mimetype="text/html", status=401)
    session["user"] = {
        "username": username,
        "role": user["role"],
        "teacher_id": user.get("teacher_id", ""),
    }
    return redirect("/")


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/api/me")
def api_me():
    return jsonify(current_user())


def load_app_config() -> dict:
    if not APP_CONFIG_PATH.exists():
        return {}
    with open(APP_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_app_config(cfg: dict) -> None:
    APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(APP_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _get_crontab() -> str:
    """读取当前用户 crontab，失败返回空字符串。"""
    try:
        r = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,  # 最多等 5 秒
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _set_crontab(content: str) -> None:
    """写入 crontab。"""
    proc = subprocess.run(
        ["crontab", "-"],
        input=content,
        text=True,
        capture_output=True,
        timeout=5,  # 最多等 5 秒，防止挂住
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "crontab write failed")


def load_all_students() -> list[dict]:
    students = []
    if not os.path.isdir(STUDENTS_DIR):
        return students
    for fname in sorted(os.listdir(STUDENTS_DIR)):
        if fname.endswith(".json") and not fname.endswith("_context.json") and fname != "roster.json" and fname != "stu_001.json":
            path = os.path.join(STUDENTS_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    students.append(json.load(f))
            except Exception:
                pass
    return students


def load_student(student_id: str) -> dict | None:
    return _store_load_student(student_id)


def save_student(student_id: str, profile: dict) -> None:
    profile.setdefault("student_id", student_id)
    _store_save_student(profile)


def student_summary(profile: dict, roster: dict | None = None) -> dict:
    history = profile.get("send_history", [])
    answered = [h for h in history if h.get("answered")]
    correct = [h for h in answered if h.get("is_correct")]
    accuracy = len(correct) / len(answered) if answered else None

    sid = profile.get("student_id")
    bindings = []
    if roster:
        bindings = roster.get("students", {}).get(sid, {}).get("bindings", [])
    # 从 roster 取姓名，画像文件名字为备用
    roster_name = None
    if roster:
        roster_name = roster.get("students", {}).get(sid, {}).get("name")

    return {
        "student_id": sid,
        "name": roster_name or profile.get("name", sid),
        "current_difficulty": profile.get("current_difficulty", 3),
        "weak_topics": profile.get("weak_topics", []),
        "total_answered": len(answered),
        "accuracy": round(accuracy * 100, 1) if accuracy is not None else None,
        "topic_mastery": profile.get("topic_mastery", {}),
        "bindings": bindings,
    }


def count_questions(teacher_id: str) -> int:
    p = BASE_DIR / "output" / teacher_id / "questions.json"
    if not p.exists():
        return 0
    try:
        with open(p, encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return 0


# ── 学生 API ──────────────────────────────────────────────────

@app.get("/api/students")
def api_students():
    roster = load_roster()
    students = load_all_students()
    tid = current_teacher_id()
    if tid:
        # teacher 只看自己绑定的学生
        students = [s for s in students if any(
            b.get("teacher_id") == tid
            for b in roster.get("students", {}).get(s.get("student_id", ""), {}).get("bindings", [])
        )]
    return jsonify([student_summary(s, roster) for s in students])


@app.get("/api/students/<student_id>")
def api_student_detail(student_id: str):
    profile = load_student(student_id)
    if not profile:
        return jsonify({"error": "student not found"}), 404
    roster = load_roster()
    return jsonify(student_summary(profile, roster))


@app.post("/api/students")
def api_student_create():
    """新增学生：写入 roster.json + 创建画像文件。"""
    data = request.get_json(silent=True) or {}
    open_id = (data.get("open_id") or "").strip()
    name = (data.get("name") or "").strip()
    bindings = data.get("bindings", [])

    if not open_id:
        return jsonify({"error": "open_id 必填"}), 400
    if not name:
        return jsonify({"error": "name 必填"}), 400

    # teacher 只能创建绑定自己的学生
    tid = current_teacher_id()
    if tid:
        bindings = [b for b in bindings if b.get("teacher_id") == tid]
        if not bindings:
            bindings = [{"teacher_id": tid, "subject": "", "chat_id": ""}]

    roster = load_roster()
    if open_id in roster["students"]:
        return jsonify({"error": "学生已存在"}), 409

    roster["students"][open_id] = {"name": name, "bindings": bindings}
    save_roster(roster)

    profile_path = Path(STUDENTS_DIR) / f"{open_id}.json"
    if not profile_path.exists():
        profile = {
            "student_id": open_id,
            "name": name,
            "teacher_id": bindings[0]["teacher_id"] if bindings else "",
            "subject": bindings[0]["subject"] if bindings else "",
            "current_difficulty": 3,
            "topic_mastery": {},
            "send_history": [],
            "weak_topics": [],
        }
        _store_save_student(profile)

    return jsonify({"ok": True, "student_id": open_id}), 201


@app.post("/api/students/<student_id>")
def api_student_update(student_id: str):
    profile = load_student(student_id)
    if not profile:
        return jsonify({"error": "student not found"}), 404

    data = request.get_json(silent=True) or {}

    if "difficulty" in data:
        diff = int(data["difficulty"])
        if 1 <= diff <= 5:
            profile["current_difficulty"] = diff

    if "weak_topics" in data:
        if isinstance(data["weak_topics"], list):
            profile["weak_topics"] = [str(t) for t in data["weak_topics"]]

    if "name" in data:
        profile["name"] = str(data["name"])
        # 同步更新 roster
        roster = load_roster()
        if student_id in roster["students"]:
            roster["students"][student_id]["name"] = str(data["name"])
            save_roster(roster)

    if "bindings" in data:
        roster = load_roster()
        if student_id in roster["students"]:
            roster["students"][student_id]["bindings"] = data["bindings"]
            save_roster(roster)

    save_student(student_id, profile)
    roster = load_roster()
    return jsonify({"ok": True, "student": student_summary(profile, roster)})


@app.delete("/api/students/<student_id>")
def api_student_delete(student_id: str):
    roster = load_roster()
    if student_id not in roster["students"]:
        return jsonify({"error": "student not found"}), 404

    del roster["students"][student_id]
    save_roster(roster)

    # 删除画像文件
    for suffix in ["", "_context"]:
        p = Path(STUDENTS_DIR) / f"{student_id}{suffix}.json"
        if p.exists():
            p.unlink()

    return jsonify({"ok": True})


@app.post("/api/students/<student_id>/toggle-push")
def api_student_toggle_push(student_id: str):
    roster = load_roster()
    if student_id not in roster.get("students", {}):
        return jsonify({"error": "student not found"}), 404
    entry = roster["students"][student_id]
    entry["push_enabled"] = not entry.get("push_enabled", True)
    save_roster(roster)
    return jsonify({"ok": True, "push_enabled": entry["push_enabled"]})


# ── 老师 API ──────────────────────────────────────────────────

@app.get("/api/teachers")
def api_teachers():
    roster = load_roster()
    teachers = roster.get("teachers", {})
    tid_filter = current_teacher_id()
    result = []
    for tid, t in teachers.items():
        if tid_filter and tid != tid_filter:
            continue
        bound = sum(
            1 for s in roster.get("students", {}).values()
            if any(b.get("teacher_id") == tid for b in s.get("bindings", []))
        )
        result.append({
            "id": tid,
            "name": t.get("name", tid),
            "subject": t.get("subject", ""),
            "open_id": t.get("open_id", ""),
            "questions_file": t.get("questions_file", ""),
            "question_count": count_questions(tid),
            "student_count": bound,
            "push_count": t.get("push_count"),
            "dedup_days": t.get("dedup_days"),
        })
    return jsonify(result)


@app.post("/api/teachers")
def api_teacher_create():
    if not is_admin():
        return _403()
    data = request.get_json(silent=True) or {}
    tid = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    subject = (data.get("subject") or "").strip()
    open_id = (data.get("open_id") or "").strip()

    if not tid:
        return jsonify({"error": "id 必填"}), 400
    if not name:
        return jsonify({"error": "name 必填"}), 400

    roster = load_roster()
    if tid in roster.get("teachers", {}):
        return jsonify({"error": "老师已存在"}), 409

    roster.setdefault("teachers", {})[tid] = {
        "open_id": open_id,
        "name": name,
        "subject": subject,
        "questions_file": f"output/{tid}/questions.json",
    }
    save_roster(roster)
    return jsonify({"ok": True, "id": tid}), 201


@app.put("/api/teachers/<teacher_id>")
def api_teacher_update(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    roster = load_roster()
    if teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher not found"}), 404

    data = request.get_json(silent=True) or {}
    t = roster["teachers"][teacher_id]

    # teacher 可以改自己的 name，admin 可以改所有字段
    if is_admin():
        editable = ("name", "subject", "open_id")
    else:
        editable = ("name",)  # teacher 只能改自己的姓名
    for field in editable:
        if field in data:
            t[field] = str(data[field])

    if "push_count" in data:
        pc = int(data["push_count"])
        if pc >= 1:
            t["push_count"] = pc

    if "dedup_days" in data:
        dd = int(data["dedup_days"])
        if dd >= 0:
            t["dedup_days"] = dd

    save_roster(roster)
    return jsonify({"ok": True})


@app.delete("/api/teachers/<teacher_id>")
def api_teacher_delete(teacher_id: str):
    if not is_admin():
        return _403()
    roster = load_roster()
    if teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher not found"}), 404

    # 统计绑定学生数，返回给前端做二次确认
    bound = [
        sid for sid, s in roster.get("students", {}).items()
        if any(b.get("teacher_id") == teacher_id for b in s.get("bindings", []))
    ]
    force = request.args.get("force") == "1"
    if bound and not force:
        return jsonify({"error": "has_students", "count": len(bound), "students": bound}), 409

    del roster["teachers"][teacher_id]
    save_roster(roster)
    return jsonify({"ok": True})


_DEFAULT_TEMPLATES = {
    "correct":   "✅ 正确！答案是 {correct_answer}，继续加油！",
    "wrong":     "❌ 答案是 {correct_answer}，你选了 {student_answer}，来看看解析吧👇",
    "no_answer": "✅ 已收到你的答案：{student_answer}（题目暂无标准答案）",
}


@app.get("/api/teachers/<teacher_id>/templates")
def api_teacher_templates_get(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    roster = load_roster()
    if teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher not found"}), 404
    tpl = roster["teachers"][teacher_id].get("message_templates", _DEFAULT_TEMPLATES)
    return jsonify({**_DEFAULT_TEMPLATES, **tpl})


@app.put("/api/teachers/<teacher_id>/templates")
def api_teacher_templates_update(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    roster = load_roster()
    if teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher not found"}), 404
    data = request.get_json(silent=True) or {}
    tpl = roster["teachers"][teacher_id].setdefault("message_templates", {})
    for key in ("correct", "wrong", "no_answer"):
        if key in data and isinstance(data[key], str):
            tpl[key] = data[key]
    save_roster(roster)
    return jsonify({"ok": True, "templates": {**_DEFAULT_TEMPLATES, **tpl}})


# ── 飞书群 API ────────────────────────────────────────────────

@app.get("/api/chats")
def api_chats():
    roster = load_roster()
    tid_filter = current_teacher_id()
    chats: dict[str, dict] = {}
    for sid, s in roster.get("students", {}).items():
        for b in s.get("bindings", []):
            cid = b.get("chat_id", "")
            if not cid:
                continue
            if tid_filter and b.get("teacher_id") != tid_filter:
                continue
            if cid not in chats:
                teacher = roster.get("teachers", {}).get(b.get("teacher_id", ""), {})
                chats[cid] = {
                    "chat_id": cid,
                    "teacher_id": b.get("teacher_id", ""),
                    "teacher_name": teacher.get("name", b.get("teacher_id", "")),
                    "subject": b.get("subject", ""),
                    "students": [],
                }
            chats[cid]["students"].append({"open_id": sid, "name": s.get("name", sid)})
    return jsonify(list(chats.values()))


# ── Roster API ────────────────────────────────────────────────

@app.get("/api/roster")
def api_roster():
    return jsonify(load_roster())


# ── 题库 API ──────────────────────────────────────────────────

@app.get("/api/questions")
def api_questions():
    roster = load_roster()
    result = {}
    for tid in roster.get("teachers", {}):
        p = BASE_DIR / "output" / tid / "questions.json"
        if not p.exists():
            result[tid] = {"total": 0, "chapters": {}, "difficulties": {}}
            continue
        with open(p, encoding="utf-8") as f:
            questions = json.load(f)
        chapters: dict[str, int] = {}
        difficulties: dict[int, int] = {}
        for q in questions:
            ch = q.get("chapter", "未分类")
            chapters[ch] = chapters.get(ch, 0) + 1
            d = q.get("difficulty", 0)
            difficulties[d] = difficulties.get(d, 0) + 1
        result[tid] = {"total": len(questions), "chapters": chapters, "difficulties": difficulties}
    return jsonify(result)


# ── 推题 & 周报 API ───────────────────────────────────────────

@app.post("/api/push/<student_id>")
def api_push(student_id: str):
    profile = load_student(student_id)
    if not profile:
        return jsonify({"error": "student not found"}), 404

    data = request.get_json(silent=True) or {}
    chapter     = data.get("chapter")       # str | None
    difficulty  = data.get("difficulty")    # int | None
    question_ids = data.get("question_ids") # list[int] | None

    # 有过滤条件时走手动推题逻辑（直接在进程内调用，避免 subprocess 传参复杂）
    if chapter or difficulty is not None or question_ids:
        try:
            import push_engine
            result = push_engine.push_manual(
                student_id,
                chapter=chapter,
                difficulty=int(difficulty) if difficulty is not None else None,
                question_ids=[int(i) for i in question_ids] if question_ids else None,
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 默认推题
    try:
        result = subprocess.run(
            [PYTHON3, "push_engine.py", "--student", student_id, "--pretty"],
            cwd=AUTO_SEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip()}), 500
        return jsonify(json.loads(result.stdout))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/report/<student_id>")
def api_report(student_id: str):
    period = request.args.get("period", "week")
    try:
        from report_generator import generate_report
        report = generate_report(student_id, period=period)
        return jsonify({"student_id": student_id, "period": period, "report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 全局配置 API ──────────────────────────────────────────────

_CONFIG_FIELDS = {
    "dedup_days":                (int,   lambda v: v >= 0),
    "mastery_threshold":         (float, lambda v: 0.0 < v < 1.0),
    "difficulty_up_threshold":   (float, lambda v: 0.0 < v < 1.0),
    "difficulty_down_threshold": (float, lambda v: 0.0 < v < 1.0),
}


@app.get("/api/config")
def api_config_get():
    if not is_admin():
        return _403()
    return jsonify(load_app_config())


@app.post("/api/config")
def api_config_update():
    if not is_admin():
        return _403()
    data = request.get_json(silent=True) or {}
    cfg = load_app_config()
    errors = {}
    for key, (cast, validate) in _CONFIG_FIELDS.items():
        if key not in data:
            continue
        try:
            val = cast(data[key])
        except (TypeError, ValueError):
            errors[key] = f"必须是 {cast.__name__} 类型"
            continue
        if not validate(val):
            errors[key] = f"值 {val} 超出合法范围"
            continue
        cfg[key] = val
    if errors:
        return jsonify({"error": "参数错误", "fields": errors}), 400
    save_app_config(cfg)
    return jsonify({"ok": True, "config": cfg})


# ── Cron 推题频率 API ─────────────────────────────────────────

@app.get("/api/cron/push-interval")
def api_cron_push_interval_get():
    if not is_admin():
        return _403()
    """返回当前推题 cron 的间隔分钟数（解析 */N 格式）。"""
    crontab = _get_crontab()
    for line in crontab.splitlines():
        if _CRON_PUSH_MARKER in line and not line.strip().startswith("#"):
            minute_field = line.strip().split()[0]
            if minute_field.startswith("*/"):
                try:
                    return jsonify({"minutes": int(minute_field[2:])})
                except ValueError:
                    pass
            elif minute_field == "0":
                return jsonify({"minutes": 60})
    return jsonify({"minutes": None, "message": "未找到推题 cron 任务"})


@app.post("/api/cron/push-interval")
def api_cron_push_interval_set():
    if not is_admin():
        return _403()
    """更新推题 cron 频率。body: {"minutes": 10}"""
    data = request.get_json(silent=True) or {}
    try:
        minutes = int(data["minutes"])
        if minutes < 1 or minutes > 1440:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "minutes 必须是 1–1440 的整数"}), 400

    crontab = _get_crontab()
    new_minute = f"*/{minutes}" if minutes < 60 else "0"
    new_lines = []
    found = False
    for line in crontab.splitlines():
        if _CRON_PUSH_MARKER in line and not line.strip().startswith("#"):
            parts = line.strip().split(None, 5)
            if len(parts) >= 6:
                parts[0] = new_minute
                new_lines.append(" ".join(parts))
                found = True
                continue
        new_lines.append(line)

    if not found:
        return jsonify({"error": "未找到推题 cron 任务，请先手动添加"}), 404

    try:
        _set_crontab("\n".join(new_lines) + "\n")
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "minutes": minutes})


# ── Cron API ──────────────────────────────────────────────────

@app.get("/api/cron")
def api_cron():
    if not CRON_PATH.exists():
        return jsonify([])
    with open(CRON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data.get("jobs", []))


def _load_cron_data() -> dict:
    if not CRON_PATH.exists():
        return {"jobs": []}
    with open(CRON_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_cron_data(data: dict) -> None:
    with open(CRON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _rebuild_command(job: dict, roster: dict) -> str:
    """根据 job 的 teacher_id / student_ids 重建 feishu_bot.py 命令行。"""
    teacher_id = job.get("teacher_id", "")
    student_ids = job.get("student_ids", [])

    # 取第一个学生的 chat_id（多学生共用同一群时取第一个）
    chat_id = ""
    for sid in student_ids:
        bindings = roster.get("students", {}).get(sid, {}).get("bindings", [])
        for b in bindings:
            if b.get("teacher_id") == teacher_id and b.get("chat_id"):
                chat_id = b["chat_id"]
                break
        if chat_id:
            break

    parts = [
        f"{AUTO_SEND_DIR}/feishu_bot.py",
        "--mode teacher",
    ]
    for sid in student_ids:
        parts.append(f"--target-id {sid}")
    if chat_id:
        parts.append(f"--chat-id {chat_id}")

    return f"{PYTHON3} " + " ".join(parts)


def _sync_crontab_for_job(job: dict) -> None:
    """将单个 job 的 schedule+command 同步到系统 crontab（后台执行，失败不阻断）。"""
    def _do_sync():
        schedule = job.get("schedule", "")
        command = job.get("command", "")
        job_id = job.get("id", "")
        enabled = job.get("enabled", True)
        env_prefix = f"AUTO_SEND_DIR={AUTO_SEND_DIR} PYTHON3_BIN={PYTHON3} "
        marker = f"# wenshu-job-{job_id}"

        crontab = _get_crontab()
        new_lines = [l for l in crontab.splitlines() if marker not in l and f"# wenshu-job-{job_id}" not in l]

        if enabled and schedule and command:
            new_lines.append(f"{schedule} {env_prefix}{command} {marker}")

        try:
            _set_crontab("\n".join(new_lines) + "\n")
        except Exception:
            pass  # crontab 写失败不阻断

    t = threading.Thread(target=_do_sync, daemon=True)
    t.start()


@app.put("/api/cron/<job_id>")
def api_cron_update(job_id: str):
    """更新 cron job 的 student_ids / name / schedule / teacher_id，并重建 command + crontab。"""
    if not is_admin():
        return _403()
    data = _load_cron_data()
    job = next((j for j in data.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "job not found"}), 404

    body = request.get_json(silent=True) or {}
    roster = load_roster()

    if "name" in body:
        job["name"] = str(body["name"]).strip()
    if "description" in body:
        job["description"] = str(body["description"]).strip()
    if "teacher_id" in body:
        tid = str(body["teacher_id"]).strip()
        if tid and tid not in roster.get("teachers", {}):
            return jsonify({"error": "teacher not found"}), 400
        job["teacher_id"] = tid
    if "student_ids" in body:
        sids = body["student_ids"]
        if not isinstance(sids, list):
            return jsonify({"error": "student_ids 必须是数组"}), 400
        job["student_ids"] = [str(s) for s in sids]
    if "schedule" in body:
        job["schedule"] = str(body["schedule"]).strip()

    # 重建 command
    job["command"] = _rebuild_command(job, roster)

    _save_cron_data(data)
    _sync_crontab_for_job(job)
    return jsonify({"ok": True, "job": job})


@app.post("/api/cron")
def api_cron_create():
    """新建 cron job，写入 cron_jobs.json 并注册到系统 crontab。"""
    if not is_admin():
        return _403()
    body = request.get_json(silent=True) or {}
    roster = load_roster()

    name = (body.get("name") or "").strip()
    teacher_id = (body.get("teacher_id") or "").strip()
    student_ids = body.get("student_ids", [])
    schedule = (body.get("schedule") or "").strip()
    description = (body.get("description") or "").strip()

    if not name:
        return jsonify({"error": "name 必填"}), 400
    if not teacher_id or teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher_id 无效"}), 400
    if not schedule:
        return jsonify({"error": "schedule 必填"}), 400

    job = {
        "id": str(uuid.uuid4()),
        "name": name,
        "enabled": True,
        "teacher_id": teacher_id,
        "student_ids": [str(s) for s in student_ids],
        "schedule": schedule,
        "description": description,
        "env": {"AUTO_SEND_DIR": AUTO_SEND_DIR, "PYTHON3_BIN": PYTHON3},
        "log": str(Path(AUTO_SEND_DIR) / "logs" / "cron_push.log"),
    }
    job["command"] = _rebuild_command(job, roster)

    data = _load_cron_data()
    data.setdefault("jobs", []).append(job)
    _save_cron_data(data)
    _sync_crontab_for_job(job)
    return jsonify({"ok": True, "job": job}), 201


@app.delete("/api/cron/<job_id>")
def api_cron_delete(job_id: str):
    """删除 cron job，同时从系统 crontab 移除。"""
    if not is_admin():
        return _403()
    data = _load_cron_data()
    jobs = data.get("jobs", [])
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "job not found"}), 404

    data["jobs"] = [j for j in jobs if j["id"] != job_id]
    _save_cron_data(data)

    # 从 crontab 移除
    marker = f"# wenshu-job-{job_id}"
    crontab = _get_crontab()
    new_lines = [l for l in crontab.splitlines() if marker not in l]
    try:
        _set_crontab("\n".join(new_lines) + "\n")
    except RuntimeError:
        pass
    return jsonify({"ok": True})


@app.post("/api/cron/<job_id>/toggle")
def api_cron_toggle(job_id: str):
    data = _load_cron_data()
    job = next((j for j in data.get("jobs", []) if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "job not found"}), 404
    job["enabled"] = not job.get("enabled", True)
    _save_cron_data(data)
    _sync_crontab_for_job(job)
    return jsonify({"ok": True, "enabled": job["enabled"]})


# ── P3-1: 题库 PDF 上传 ───────────────────────────────────────

# 内存任务表：task_id -> {status, log, teacher_id}
_upload_tasks: dict[str, dict] = {}
_UPLOAD_DIR = Path(AUTO_SEND_DIR) / "uploads"


def _run_process_pdf(task_id: str, teacher_id: str, subject: str, pdf_path: str) -> None:
    task = _upload_tasks[task_id]
    out_dir = str(BASE_DIR / "output" / teacher_id)
    cmd = [
        PYTHON3, "process_pdf.py",
        "--pdf", pdf_path,
        "--out", out_dir,
        "--parse-questions",
        "--teacher-id", teacher_id,
    ]
    if subject:
        cmd += ["--subject", subject]
    task["status"] = "running"
    task["log"] = ""
    try:
        proc = subprocess.Popen(
            cmd, cwd=AUTO_SEND_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            task["log"] += line
        proc.wait()
        task["status"] = "done" if proc.returncode == 0 else "error"
        task["returncode"] = proc.returncode
    except Exception as e:
        task["status"] = "error"
        task["log"] += f"\n[exception] {e}"
    finally:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/api/questions/<teacher_id>/upload")
def api_questions_upload(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    roster = load_roster()
    if teacher_id not in roster.get("teachers", {}):
        return jsonify({"error": "teacher not found"}), 404
    if "file" not in request.files:
        return jsonify({"error": "缺少 file 字段"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "只支持 PDF 文件"}), 400

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = str(_UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf")
    f.save(tmp_path)

    subject = roster["teachers"][teacher_id].get("subject", "")
    task_id = uuid.uuid4().hex
    _upload_tasks[task_id] = {"status": "pending", "log": "", "teacher_id": teacher_id}

    t = threading.Thread(target=_run_process_pdf, args=(task_id, teacher_id, subject, tmp_path), daemon=True)
    t.start()
    return jsonify({"ok": True, "task_id": task_id}), 202


@app.get("/api/questions/<teacher_id>/status")
def api_questions_status(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    task_id = request.args.get("task_id", "")
    task = _upload_tasks.get(task_id)
    if not task or task.get("teacher_id") != teacher_id:
        return jsonify({"error": "task not found"}), 404
    return jsonify({
        "task_id": task_id,
        "status": task["status"],
        "log_tail": task["log"][-2000:],
    })


@app.get("/api/questions/<teacher_id>/preview")
def api_questions_preview(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    p = BASE_DIR / "output" / teacher_id / "questions.json"
    if not p.exists():
        return jsonify({"error": "题库不存在，请先上传 PDF"}), 404
    with open(p, encoding="utf-8") as f:
        all_questions = json.load(f)

    # ── 过滤参数 ─────────────────────────────────────────────
    page      = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    chapter   = request.args.get("chapter", "").strip()
    difficulty = request.args.get("difficulty", "").strip()
    search    = request.args.get("search", "").strip().lower()

    pool = all_questions
    if chapter:
        pool = [q for q in pool if q.get("chapter", "") == chapter]
    if difficulty:
        pool = [q for q in pool if str(q.get("difficulty", "")) == difficulty]
    if search:
        pool = [
            q for q in pool
            if search in (q.get("question_text", "") + " " + " ".join(q.get("topic_tags", []))).lower()
        ]

    total       = len(pool)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start       = (page - 1) * page_size
    items       = pool[start : start + page_size]

    # 所有章节列表（供前端下拉使用）
    chapters = sorted(set(q.get("chapter", "") for q in all_questions if q.get("chapter")))

    return jsonify({
        "teacher_id":  teacher_id,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
        "questions":   items,
        "chapters":    chapters,
    })


# ── 题目编辑 ──────────────────────────────────────────────────

_EDITABLE_Q_FIELDS = {"chapter", "topic_tags", "difficulty", "question_type", "correct_answer"}


@app.put("/api/questions/<teacher_id>/<int:question_id>")
def api_question_update(teacher_id: str, question_id: int):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    p = BASE_DIR / "output" / teacher_id / "questions.json"
    if not p.exists():
        return jsonify({"error": "题库不存在"}), 404
    with open(p, encoding="utf-8") as f:
        questions = json.load(f)
    q = next((q for q in questions if q["id"] == question_id), None)
    if not q:
        return jsonify({"error": "题目不存在"}), 404

    data = request.get_json(silent=True) or {}
    for field in _EDITABLE_Q_FIELDS:
        if field not in data:
            continue
        if field == "difficulty":
            try:
                val = int(data[field])
                if 1 <= val <= 5:
                    q[field] = val
            except (TypeError, ValueError):
                pass
        elif field == "topic_tags":
            if isinstance(data[field], list):
                q[field] = [str(t).strip() for t in data[field] if str(t).strip()]
        else:
            q[field] = str(data[field]).strip()

    with open(p, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "question": q})


# ── 题库完整性检测 ────────────────────────────────────────────

import re as _re


@app.get("/api/questions/<teacher_id>/integrity")
def api_questions_integrity(teacher_id: str):
    tid = current_teacher_id()
    if tid and tid != teacher_id:
        return _403()
    index_path = BASE_DIR / "output" / "index.md"
    expected = None
    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
        # 匹配 index.md 中 | chenxi | AP统计 | 来源文件 | 185 | 这样的行
        m = _re.search(
            rf'\|\s*{_re.escape(teacher_id)}\s*\|[^|]+\|[^|]+\|\s*(\d+)\s*\|',
            text,
        )
        if m:
            expected = int(m.group(1))
    actual = count_questions(teacher_id)
    missing = (expected - actual) if expected is not None else None
    return jsonify({
        "teacher_id": teacher_id,
        "expected":   expected,
        "actual":     actual,
        "missing":    missing,
        "ok":         expected is None or actual >= expected,
    })


# ── P3-2: lark-cli 认证状态 ───────────────────────────────────

@app.get("/api/lark/auth-status")
def api_lark_auth_status():
    if not is_admin():
        return _403()
    from lark_cli_send import LARK_CLI_BIN, _FEISHU_DIR
    try:
        proc = subprocess.run(
            [LARK_CLI_BIN, "auth", "status", "--as", "bot"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "LARK_CLI_CONFIG_DIR": str(_FEISHU_DIR)},
        )
        output = (proc.stdout + proc.stderr).strip()
        return jsonify({"ok": proc.returncode == 0, "output": output})
    except Exception as e:
        return jsonify({"ok": False, "output": str(e)}), 500


# ── P3-3: 日志查看 ────────────────────────────────────────────

_LOG_FILES = {
    "push":   "push_events.jsonl",
    "answer": "answer_events.jsonl",
}


@app.get("/api/logs")
def api_logs():
    if not is_admin():
        return _403()
    log_type = request.args.get("type", "push")
    limit = min(int(request.args.get("limit", 50)), 200)
    fname = _LOG_FILES.get(log_type)
    if not fname:
        return jsonify({"error": "type 必须是 push 或 answer"}), 400
    log_path = BASE_DIR / "logs" / fname
    if not log_path.exists():
        return jsonify({"type": log_type, "records": []})
    records = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return jsonify({"type": log_type, "records": records[-limit:]})


# ── 图片服务 ──────────────────────────────────────────────────

@app.get("/api/image")
def api_image():
    """
    提供题目/解析图片访问。
    ?path=output/chenxi/images/questions/p0006_q1.png
    只允许访问 BASE_DIR/output/ 下的图片，防止路径穿越。
    """
    rel = request.args.get("path", "")
    if not rel:
        return jsonify({"error": "缺少 path 参数"}), 400

    # 安全检查：只允许 output/ 目录下的文件
    target = (BASE_DIR / rel).resolve()
    allowed = (BASE_DIR / "output").resolve()
    if not str(target).startswith(str(allowed)):
        return jsonify({"error": "路径不合法"}), 403

    if not target.exists():
        return jsonify({"error": "文件不存在"}), 404

    suffix = target.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(suffix[1:], "image/png")
    with open(target, "rb") as f:
        return Response(f.read(), mimetype=mime)


# ── 记忆 API ──────────────────────────────────────────────────

@app.get("/api/memory/<student_id>")
def api_memory_list(student_id: str):
    """列出学生所有记忆文件（教学反馈、学习进展、学生画像）。"""
    from memory_store import list_student_memories, MEMORY_ROOT
    memories = list_student_memories(student_id)
    return jsonify({
        "student_id": student_id,
        "memories": [
            {
                "type":       m["type"],
                "meta":       m["meta"],
                "content":    m["content"],
                "path":       m["path"],
            }
            for m in memories
        ],
        "memory_root": str(MEMORY_ROOT),
    })


@app.put("/api/memory/<student_id>/<memory_type>")
def api_memory_write(student_id: str, memory_type: str):
    """
    创建或覆盖学生指定类型的记忆文件。
    Body: {"content": "...", "student_name": "..."}
    """
    from memory_store import write_student_memory
    data = request.get_json(silent=True) or {}
    content = data.get("content", "").strip()
    student_name = data.get("student_name", "")

    if not content:
        return jsonify({"error": "content 不能为空"}), 400

    try:
        path = write_student_memory(
            student_id, memory_type, content,
            student_name=student_name
        )
        return jsonify({"ok": True, "path": str(path)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/memory/<student_id>/append-feedback")
def api_memory_append_feedback(student_id: str):
    """
    向学生「教学反馈」追加一条洞察（无需覆盖全文）。
    Body: {"insight": "...", "student_name": "..."}
    """
    from memory_store import append_teaching_feedback
    data = request.get_json(silent=True) or {}
    insight = data.get("insight", "").strip()
    student_name = data.get("student_name", "")

    if not insight:
        return jsonify({"error": "insight 不能为空"}), 400

    try:
        append_teaching_feedback(student_id, insight, student_name)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/memory/<student_id>/consolidate")
def api_memory_consolidate(student_id: str):
    """
    调用 LLM（Claude Haiku）对学生记忆进行精选整合。
    需要环境变量 ANTHROPIC_API_KEY 或 Body: {"api_key": "..."}
    """
    from memory_store import consolidate_memories, update_global_index
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "")

    try:
        result = consolidate_memories(student_id, api_key=api_key)
        update_global_index()
        if not result:
            return jsonify({"ok": False, "message": "无可整合内容或缺少 API Key"}), 200
        return jsonify({"ok": True, "consolidated_length": len(result)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/memory-index")
def api_memory_index():
    """返回全局 TEACHING_MEMORY.md 索引内容。"""
    from memory_store import MEMORY_INDEX, update_global_index
    try:
        update_global_index()
    except Exception:
        pass
    if not MEMORY_INDEX.exists():
        return jsonify({"content": "（索引文件不存在）"})
    with open(MEMORY_INDEX, encoding="utf-8") as f:
        return jsonify({"content": f.read()})


# ── 作业任务管理 ──────────────────────────────────────────────

@app.get("/api/assignments")
@require_login()
def api_assignments_list():
    """列出作业（老师只能看自己的，admin 看全部）。"""
    import task_store
    tid = current_teacher_id() if not is_admin() else None
    assignments = task_store.list_assignments(teacher_id=tid)
    result = []
    for a in assignments:
        summ = task_store.assignment_summary(a)
        result.append({**a, **summ})
    return jsonify(result)


@app.post("/api/assignments")
@require_login()
def api_assignments_create():
    """
    创建作业并向所有绑定学生发送飞书通知。

    Body JSON:
      name         str   作业名称（必填）
      due_date     str   截止日期 YYYY-MM-DD（必填）
      description  str   作业说明（可选）
      question_ids list  指定题目 ID 列表（可选）
      chapter      str   按章节过滤（question_ids 为空时生效）
      difficulty   int   难度过滤（可选）
      student_ids  list  指定学生（不填则取老师所有绑定学生）
    """
    import task_store
    from student_store import load_roster

    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    due_date = (body.get("due_date") or "").strip()
    if not name:
        return jsonify({"error": "name 不能为空"}), 400
    if not due_date:
        return jsonify({"error": "due_date 不能为空"}), 400

    teacher_id = current_teacher_id()
    if not teacher_id and not is_admin():
        return jsonify({"error": "无法确定 teacher_id"}), 403

    # 确定目标学生
    roster = load_roster()
    all_students = roster.get("students", {})
    if body.get("student_ids"):
        target_sids = body["student_ids"]
    else:
        # 取该老师所有绑定学生
        target_sids = [
            sid for sid, info in all_students.items()
            if any(b.get("teacher_id") == teacher_id for b in info.get("bindings", []))
        ]

    if not target_sids:
        return jsonify({"error": "没有找到绑定学生"}), 400

    # 构造 student_bindings（找到每个学生对应该老师的 chat_id）
    student_bindings = []
    for sid in target_sids:
        info = all_students.get(sid, {})
        binding = next(
            (b for b in info.get("bindings", []) if b.get("teacher_id") == teacher_id),
            info.get("bindings", [{}])[0] if info.get("bindings") else {}
        )
        student_bindings.append({"student_id": sid, "chat_id": binding.get("chat_id", "")})

    # 计算题数（若指定了 question_ids）
    question_ids: list[int] = [int(i) for i in (body.get("question_ids") or [])]

    assignment = task_store.create_assignment(
        name=name,
        teacher_id=teacher_id or "admin",
        student_bindings=student_bindings,
        due_date=due_date,
        description=body.get("description", ""),
        question_ids=question_ids or None,
        chapter=body.get("chapter") or None,
        difficulty=int(body["difficulty"]) if body.get("difficulty") else None,
    )

    # 向学生发送飞书通知
    notify_errors = []
    for b in student_bindings:
        sid = b["student_id"]
        chat_id = b.get("chat_id", "")
        if not chat_id:
            continue
        try:
            _send_assignment_notify(assignment, sid, chat_id)
            task_store.mark_student_notified(assignment["id"], sid)
        except Exception as e:
            notify_errors.append(f"{sid}: {e}")

    resp = {"assignment": assignment, "notified": len(student_bindings) - len(notify_errors)}
    if notify_errors:
        resp["notify_errors"] = notify_errors
    return jsonify(resp), 201


@app.delete("/api/assignments/<assignment_id>")
@require_login()
def api_assignments_delete(assignment_id: str):
    """删除作业。"""
    import task_store
    a = task_store.get_assignment(assignment_id)
    if not a:
        return jsonify({"error": "作业不存在"}), 404
    if not is_admin() and a.get("teacher_id") != current_teacher_id():
        return jsonify({"error": "无权删除"}), 403
    task_store.delete_assignment(assignment_id)
    return jsonify({"ok": True})


@app.get("/api/assignments/<assignment_id>")
@require_login()
def api_assignment_detail(assignment_id: str):
    """获取作业详情（含各学生进度）。"""
    import task_store
    a = task_store.get_assignment(assignment_id)
    if not a:
        return jsonify({"error": "作业不存在"}), 404
    summ = task_store.assignment_summary(a)
    return jsonify({**a, **summ})


@app.post("/api/assignments/<assignment_id>/remind")
@require_login()
def api_assignment_remind(assignment_id: str):
    """手动向未完成的学生发送提醒。"""
    import task_store
    a = task_store.get_assignment(assignment_id)
    if not a:
        return jsonify({"error": "作业不存在"}), 404
    if not is_admin() and a.get("teacher_id") != current_teacher_id():
        return jsonify({"error": "无权操作"}), 403

    reminded = 0
    errors = []
    for sid, sa in a["student_assignments"].items():
        if sa["status"] in ("completed", "overdue"):
            continue
        chat_id = sa.get("chat_id", "")
        if not chat_id:
            continue
        try:
            _send_assignment_reminder(a, sid, chat_id, sa)
            task_store.mark_student_reminded(assignment_id, sid)
            reminded += 1
        except Exception as e:
            errors.append(f"{sid}: {e}")

    return jsonify({"reminded": reminded, "errors": errors})


def _send_assignment_notify(assignment: dict, student_id: str, chat_id: str) -> None:
    """向学生发送新作业通知。"""
    import lark_cli_send as lcs
    from student_store import load_roster
    roster = load_roster()
    student_name = roster.get("students", {}).get(student_id, {}).get("name", student_id)

    due = assignment.get("due_date", "")
    total = assignment["student_assignments"][student_id].get("total", 0)
    total_str = f"{total} 道题" if total else ""
    desc = assignment.get("description", "")

    lines = [
        f"📋 {student_name}，你有一个新作业！",
        f"",
        f"📌 作业名称：{assignment['name']}",
    ]
    if desc:
        lines.append(f"📝 说明：{desc}")
    if total_str:
        lines.append(f"📚 题目数量：{total_str}")
    if due:
        lines.append(f"⏰ 截止日期：{due}")
    lines.extend([
        f"",
        f"发送 /任务 查看和开始作业",
    ])
    lcs.send_text(chat_id, "\n".join(lines))


def _send_assignment_reminder(assignment: dict, student_id: str, chat_id: str, sa: dict) -> None:
    """向学生发送作业截止提醒。"""
    import lark_cli_send as lcs
    from student_store import load_roster
    from datetime import date as _date
    roster = load_roster()
    student_name = roster.get("students", {}).get(student_id, {}).get("name", student_id)

    due = assignment.get("due_date", "")
    progress = sa.get("progress", 0)
    total = sa.get("total", 0)
    progress_str = f"（已完成 {progress}/{total} 道）" if total else ""

    days_left = ""
    if due:
        try:
            delta = (_date.fromisoformat(due) - _date.today()).days
            days_left = f"还有 {delta} 天" if delta > 0 else "今天截止"
        except Exception:
            pass

    msg = (
        f"⏰ {student_name}，作业「{assignment['name']}」{days_left}到期{progress_str}，"
        f"发送 /任务 继续完成吧！"
    )
    lcs.send_text(chat_id, msg)


# ── 管理界面 ──────────────────────────────────────────────────

@app.get("/")
def admin_ui():
    html_path = os.path.join(AUTO_SEND_DIR, "templates", "admin.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/html")
    return Response("<h1>templates/admin.html not found</h1>", mimetype="text/html"), 404


# ── 主入口 ────────────────────────────────────────────────────

if __name__ == "__main__":
    _init_default_users()
    port = int(os.getenv("ADMIN_PORT", "10187"))
    print(f"管理后台启动：http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
