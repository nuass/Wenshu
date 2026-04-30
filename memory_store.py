#!/usr/bin/env python3
"""
memory_store.py

Wenshu 飞书推题系统 — 持久记忆层

提供四类教学记忆的读写、目录初始化、索引维护和 LLM 精选整合功能。

记忆类型：
    student_profile    — 学生画像（性格、习惯、偏好、目标）
    teaching_feedback  — 教学反馈（答题规律洞察、纠错模式、进步信号）
    learning_progress  — 学习进展（章节攻克情况、阶段目标、里程碑）
    teaching_resource  — 教学资源（题库特征备注、外部资料链接）老师级共享

目录结构：
    memory/
    ├── TEACHING_MEMORY.md           全局索引
    ├── teachers/<teacher_id>/
    │   └── teacher_profile.md
    └── students/<open_id>/
        ├── 学生画像.md
        ├── 教学反馈.md
        └── 学习进展.md

导出接口：
    ensure_memory_dirs()
    write_memory_file(path, frontmatter, content)
    read_memory_file(path) -> dict | None
    list_student_memories(student_id) -> list[dict]
    get_memory_path(student_id, memory_type) -> Path
    write_student_memory(student_id, memory_type, content, meta) -> Path
    read_student_memory(student_id, memory_type) -> dict | None
    append_teaching_feedback(student_id, insight, student_name) -> None
    update_learning_progress(student_id, update_text, student_name) -> None
    update_global_index() -> None
    get_memory_summary(student_id) -> str
    search_memories(student_id, keyword, memory_types) -> list[dict]
    score_memory_importance(student_id, memory_type) -> float
    cleanup_old_memories(student_id, days, auto_archive) -> dict
    consolidate_memories(student_id) -> str
    add_memory_tags(student_id, memory_type, tags) -> None
    get_memories_by_tag(student_id, tag) -> list[dict]
    get_memory_stats(student_id) -> dict
    search_memories(student_id, keyword) -> list[dict]
    score_memory_importance(student_id, memory_type) -> float
    cleanup_old_memories(student_id, days) -> None
    consolidate_memories(student_id) -> str
"""

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# ── 路径配置 ──────────────────────────────────────────────────

# 相对于本文件所在目录（Wenshu-main/）
_BASE_DIR = Path(__file__).parent
MEMORY_ROOT = _BASE_DIR / "memory"
MEMORY_INDEX = MEMORY_ROOT / "TEACHING_MEMORY.md"

# 记忆类型 → 文件名映射
_MEMORY_FILENAMES = {
    "student_profile":   "学生画像.md",
    "teaching_feedback": "教学反馈.md",
    "learning_progress": "学习进展.md",
}

_MAX_INDEX_LINES = 200  # 全局索引最大行数
_MAX_INDEX_BYTES = 25 * 1024  # 25 KB


# ── 目录初始化 ────────────────────────────────────────────────

def ensure_memory_dirs() -> None:
    """确保 memory/ 顶层目录和子目录存在；幂等，可重复调用。"""
    for subdir in ["teachers", "students"]:
        (MEMORY_ROOT / subdir).mkdir(parents=True, exist_ok=True)


# ── Frontmatter 解析 / 序列化 ─────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    从 Markdown 文本中解析 YAML-like frontmatter。
    返回 (meta_dict, body_content)。
    只支持简单 key: value 格式（不依赖 PyYAML）。
    """
    if not text.startswith("---"):
        return {}, text

    lines = text.split("\n")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, text

    meta: dict = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")
    return meta, body


def _serialize_frontmatter(meta: dict, body: str) -> str:
    """将 meta 字典 + body 文本序列化为带 frontmatter 的 Markdown。"""
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


# ── 基础读写 ──────────────────────────────────────────────────

def write_memory_file(path: Path, frontmatter: dict, content: str) -> None:
    """
    将 frontmatter + content 写入指定路径的 .md 文件。
    自动创建父目录。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _serialize_frontmatter(frontmatter, content)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_memory_file(path: Path) -> Optional[dict]:
    """
    读取并解析 .md 记忆文件。
    返回 {"meta": dict, "content": str, "path": str}；
    文件不存在时返回 None。
    """
    path = Path(path)
    if not path.exists():
        return None

    # 尝试多种编码读取
    text = ""
    for encoding in ["utf-8", "gbk", "latin-1"]:
        try:
            with open(path, encoding=encoding) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue

    if not text:
        return None

    meta, content = _parse_frontmatter(text)
    return {"meta": meta, "content": content, "path": str(path)}


# ── 学生记忆路径 ──────────────────────────────────────────────

def get_memory_path(student_id: str, memory_type: str) -> Path:
    """
    返回学生特定类型记忆文件的绝对路径。

    Args:
        student_id:  学生 open_id
        memory_type: student_profile | teaching_feedback | learning_progress
    """
    filename = _MEMORY_FILENAMES.get(memory_type)
    if not filename:
        raise ValueError(
            f"未知记忆类型: {memory_type}，"
            f"支持: {list(_MEMORY_FILENAMES.keys())}"
        )
    return MEMORY_ROOT / "students" / student_id / filename


def get_teacher_memory_path(teacher_id: str) -> Path:
    """返回老师记忆文件路径。"""
    return MEMORY_ROOT / "teachers" / teacher_id / "teacher_profile.md"


# ── 学生记忆读写（类型化） ────────────────────────────────────

def write_student_memory(
    student_id: str,
    memory_type: str,
    content: str,
    meta: Optional[dict] = None,
    student_name: str = "",
) -> Path:
    """
    写入学生记忆文件（覆盖）。

    Args:
        student_id:   学生 open_id
        memory_type:  记忆类型（见 _MEMORY_FILENAMES）
        content:      Markdown 正文
        meta:         额外 frontmatter 字段（可选）
        student_name: 学生名字，用于生成 name 字段（可选）

    Returns:
        写入的文件 Path
    """
    _type_names = {
        "student_profile":   "学生画像",
        "teaching_feedback": "教学反馈",
        "learning_progress": "学习进展",
    }
    display = _type_names.get(memory_type, memory_type)
    name_label = f"{student_name}的{display}" if student_name else display

    frontmatter = {
        "name":        name_label,
        "description": f"追踪学生 {student_id} 的{display}",
        "type":        memory_type,
        "student_id":  student_id,
        "updated_at":  str(date.today()),
    }
    if meta:
        frontmatter.update(meta)

    path = get_memory_path(student_id, memory_type)
    write_memory_file(path, frontmatter, content)
    return path


def read_student_memory(student_id: str, memory_type: str) -> Optional[dict]:
    """
    读取学生记忆文件。

    Returns:
        {"meta": dict, "content": str, "path": str} 或 None（文件不存在）
    """
    path = get_memory_path(student_id, memory_type)
    return read_memory_file(path)


def list_student_memories(student_id: str) -> list[dict]:
    """
    列出学生所有已存在的记忆文件。

    Returns:
        list of {"type": str, "meta": dict, "content": str, "path": str}
    """
    results = []
    for memory_type in _MEMORY_FILENAMES:
        mem = read_student_memory(student_id, memory_type)
        if mem:
            mem["type"] = memory_type
            results.append(mem)
    return results


# ── 事件驱动：追加教学反馈 ────────────────────────────────────

def append_teaching_feedback(
    student_id: str,
    insight: str,
    student_name: str = "",
) -> None:
    """
    在学生「教学反馈」记忆中追加一条新洞察。
    若文件不存在则自动创建。

    Args:
        student_id:   学生 open_id
        insight:      本次需要记录的教学洞察（Markdown 格式）
        student_name: 学生名字（用于首次创建时填写 name 字段）
    """
    existing = read_student_memory(student_id, "teaching_feedback")
    today = str(date.today())

    new_entry = f"\n### {today}\n\n{insight.strip()}\n"

    if existing:
        updated_content = existing["content"].rstrip() + "\n" + new_entry
        meta = existing["meta"]
        meta["updated_at"] = today
        path = get_memory_path(student_id, "teaching_feedback")
        write_memory_file(path, meta, updated_content)
    else:
        initial_content = (
            "## 教学洞察\n\n"
            "> 记录答题规律、纠错模式、进步信号等关键教学观察。\n"
            + new_entry
        )
        write_student_memory(
            student_id, "teaching_feedback", initial_content,
            student_name=student_name
        )


def update_learning_progress(
    student_id: str,
    update_text: str,
    student_name: str = "",
) -> None:
    """
    更新学生「学习进展」记忆（追加新进展条目）。
    若文件不存在则自动创建。

    Args:
        student_id:   学生 open_id
        update_text:  本次进展更新（Markdown 格式）
        student_name: 学生名字
    """
    existing = read_student_memory(student_id, "learning_progress")
    today = str(date.today())

    new_entry = f"\n### {today}\n\n{update_text.strip()}\n"

    if existing:
        updated_content = existing["content"].rstrip() + "\n" + new_entry
        meta = existing["meta"]
        meta["updated_at"] = today
        path = get_memory_path(student_id, "learning_progress")
        write_memory_file(path, meta, updated_content)
    else:
        initial_content = (
            "## 学习进展\n\n"
            "> 记录章节攻克情况、阶段目标、里程碑事件。\n"
            + new_entry
        )
        write_student_memory(
            student_id, "learning_progress", initial_content,
            student_name=student_name
        )


# ── 记忆摘要（供推题引擎使用）────────────────────────────────

def get_memory_summary(student_id: str) -> str:
    """
    返回学生所有记忆的简洁文本摘要（注入推题/意图路由上下文用）。
    若无记忆文件则返回空字符串。
    """
    memories = list_student_memories(student_id)
    if not memories:
        return ""

    _type_labels = {
        "student_profile":   "📋 学生画像",
        "teaching_feedback": "💡 教学反馈",
        "learning_progress": "📈 学习进展",
    }

    parts = ["=== 学生历史记忆 ==="]
    for mem in memories:
        label = _type_labels.get(mem["type"], mem["type"])
        # 取正文前 500 字符，避免上下文过长
        snippet = mem["content"][:500].strip()
        if len(mem["content"]) > 500:
            snippet += "…（截断）"
        parts.append(f"\n{label}\n{snippet}")

    return "\n".join(parts)


# ── 全局索引维护 ──────────────────────────────────────────────

def update_global_index() -> None:
    """
    扫描所有学生/老师记忆文件，重建 TEACHING_MEMORY.md 全局索引。
    索引超过 _MAX_INDEX_LINES 行或 _MAX_INDEX_BYTES 字节时自动截断旧条目。
    """
    ensure_memory_dirs()

    lines = [
        "# Teaching Memory Index",
        "",
        f"> 自动生成于 {date.today()}，请勿手动编辑此文件。",
        "",
        "## 学生记忆",
        "",
    ]

    students_dir = MEMORY_ROOT / "students"
    if students_dir.exists():
        for student_dir in sorted(students_dir.iterdir()):
            if not student_dir.is_dir():
                continue
            student_id = student_dir.name
            entries = []
            for memory_type, filename in _MEMORY_FILENAMES.items():
                fpath = student_dir / filename
                if fpath.exists():
                    mem = read_memory_file(fpath)
                    if mem:
                        name = mem["meta"].get("name", filename)
                        updated = mem["meta"].get("updated_at", "")
                        entries.append(f"  - [{name}](students/{student_id}/{filename}) — {updated}")
            if entries:
                lines.append(f"### `{student_id}`")
                lines.extend(entries)
                lines.append("")

    lines.append("## 老师教学资源")
    lines.append("")

    teachers_dir = MEMORY_ROOT / "teachers"
    if teachers_dir.exists():
        for teacher_dir in sorted(teachers_dir.iterdir()):
            if not teacher_dir.is_dir():
                continue
            teacher_id = teacher_dir.name
            entries = []
            for fpath in sorted(teacher_dir.glob("*.md")):
                mem = read_memory_file(fpath)
                if mem:
                    name = mem["meta"].get("name", fpath.name)
                    updated = mem["meta"].get("updated_at", "")
                    rel = f"teachers/{teacher_id}/{fpath.name}"
                    entries.append(f"  - [{name}]({rel}) — {updated}")
            if entries:
                lines.append(f"### `{teacher_id}`")
                lines.extend(entries)
                lines.append("")

    content = "\n".join(lines)

    # 截断保护
    if len(content.encode("utf-8")) > _MAX_INDEX_BYTES:
        content_lines = content.split("\n")
        content = "\n".join(content_lines[:_MAX_INDEX_LINES])
        content += f"\n\n> ⚠️ 索引已截断（超出 {_MAX_INDEX_BYTES // 1024}KB 限制）\n"

    MEMORY_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_INDEX, "w", encoding="utf-8") as f:
        f.write(content)


# ── LLM 精选整合（可选）──────────────────────────────────────

def consolidate_memories(student_id: str) -> str:
    """
    调用 LLM 对学生教学反馈记忆进行精选整合，
    去除过时/重复条目，保留高价值洞察。

    使用 UNIAPI 调用模型，从 config 读取配置。

    Args:
        student_id: 学生 open_id

    Returns:
        整合后的新记忆内容（str），同时写入文件。
        若 API 不可用则返回原始内容（不做修改）。
    """
    from config import UNIAPI_KEY, UNIAPI_BASE, API_MODEL
    if not UNIAPI_KEY:
        return ""

    feedback = read_student_memory(student_id, "teaching_feedback")
    progress = read_student_memory(student_id, "learning_progress")

    if not feedback and not progress:
        return ""

    try:
        from openai import OpenAI
    except ImportError:
        return ""

    client = OpenAI(api_key=UNIAPI_KEY, base_url=UNIAPI_BASE)

    existing_feedback = feedback["content"] if feedback else "（暂无）"
    existing_progress = progress["content"] if progress else "（暂无）"

    prompt = f"""你是一位资深 AP 统计学教师，正在整理学生的教学记忆档案。

请对以下「教学反馈」记忆进行精选整合：
1. 合并重复或相似的条目
2. 删除已过时或已解决的问题（如「章节已攻克」后的旧困难描述）
3. 保留所有仍然相关的洞察
4. 突出最近 30 天内出现的规律
5. 以 Markdown 格式输出，保持原有章节结构

原始教学反馈：
{existing_feedback[:2000]}

原始学习进展：
{existing_progress[:1000]}

请只输出整合后的「教学反馈」Markdown 正文内容，不包含 frontmatter。"""

    try:
        response = client.chat.completions.create(
            model=API_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.3
        )
        consolidated = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[memory_store] 整合记忆失败: {e}")
        return existing_feedback if feedback else ""

    # 写回整合结果
    if feedback:
        meta = feedback["meta"]
        meta["updated_at"] = str(date.today())
        meta["consolidated"] = "true"
        meta["consolidated_at"] = str(date.today())
        path = get_memory_path(student_id, "teaching_feedback")
        write_memory_file(path, meta, consolidated)

    # 更新全局索引
    try:
        update_global_index()
    except Exception:
        pass

    return consolidated


# ── 记忆搜索功能 ──────────────────────────────────────────────

def search_memories(student_id: str, keyword: str, memory_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    按关键词检索学生记忆。

    Args:
        student_id:    学生 open_id
        keyword:       搜索关键词
        memory_types:  限定搜索的记忆类型列表（默认搜索所有类型）

    Returns:
        list of {"type": str, "meta": dict, "content": str, "path": str, "matches": list}
    """
    if not memory_types:
        memory_types = list(_MEMORY_FILENAMES.keys())

    results = []
    keyword_lower = keyword.lower()

    for memory_type in memory_types:
        mem = read_student_memory(student_id, memory_type)
        if not mem:
            continue

        # 搜索匹配
        content_lower = mem["content"].lower()
        if keyword_lower in content_lower:
            # 提取匹配的上下文
            matches = []
            lines = mem["content"].split("\n")
            for i, line in enumerate(lines):
                if keyword_lower in line.lower():
                    # 提取前后各 2 行作为上下文
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    context = "\n".join(lines[start:end])
                    matches.append({
                        "line": i + 1,
                        "text": line.strip(),
                        "context": context
                    })

            mem["type"] = memory_type
            mem["matches"] = matches
            results.append(mem)

    return results


# ── 记忆重要性评分 ────────────────────────────────────────────

def score_memory_importance(student_id: str, memory_type: str) -> float:
    """
    评分记忆重要性 (0.0 - 1.0)。

    评分规则：
    - 近期记忆权重高
    - 错题相关权重高
    - 包含知识点掌握状态权重高
    - 标记为重要的权重高

    Args:
        student_id:   学生 open_id
        memory_type:  记忆类型

    Returns:
        重要性评分 (0.0 - 1.0)
    """
    mem = read_student_memory(student_id, memory_type)
    if not mem:
        return 0.0

    score = 0.5  # 基础分

    # 1. 更新时间
    updated_at = mem["meta"].get("updated_at", "")
    if updated_at:
        try:
            update_date = date.fromisoformat(updated_at)
            days_since = (date.today() - update_date).days
            if days_since <= 7:
                score += 0.2
            elif days_since <= 30:
                score += 0.1
        except ValueError:
            pass

    # 2. 内容关键词分析
    content_lower = mem["content"].lower()
    important_keywords = [
        "错误", "错题", "不会", "困难", "混淆", "薄弱",
        "掌握", "理解", "进步", "提升", "攻克",
        "重要", "关键", "重点", "必考", "高频",
    ]
    for keyword in important_keywords:
        if keyword in content_lower:
            score += 0.05

    # 3. Meta 标记
    if mem["meta"].get("important") == "true":
        score += 0.2
    if mem["meta"].get("consolidated") == "true":
        score += 0.1

    return min(1.0, score)


# ── 记忆过期清理 ──────────────────────────────────────────────

def cleanup_old_memories(student_id: str, days: int = 90, auto_archive: bool = True) -> Dict[str, int]:
    """
    清理过期记忆。

    Args:
        student_id:   学生 open_id
        days:         过期天数（默认 90 天）
        auto_archive: 是否自动归档（否则删除）

    Returns:
        {"archived": int, "deleted": int, "skipped": int}
    """
    result = {"archived": 0, "deleted": 0, "skipped": 0}

    for memory_type in _MEMORY_FILENAMES.keys():
        mem = read_student_memory(student_id, memory_type)
        if not mem:
            continue

        # 检查更新时间
        updated_at = mem["meta"].get("updated_at", "")
        if not updated_at:
            result["skipped"] += 1
            continue

        try:
            update_date = date.fromisoformat(updated_at)
            days_since = (date.today() - update_date).days

            if days_since <= days:
                result["skipped"] += 1
                continue

            # 检查重要性，重要的不删除
            importance = score_memory_importance(student_id, memory_type)
            if importance > 0.7:
                result["skipped"] += 1
                continue

            path = get_memory_path(student_id, memory_type)

            if auto_archive:
                # 归档到 archive 目录
                archive_dir = MEMORY_ROOT / "archive" / student_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / f"{Path(path).name}.{date.today()}.archived"
                path.rename(archive_path)

                # 创建归档标记
                meta = mem["meta"]
                meta["archived_at"] = str(date.today())
                meta["archived_from"] = str(path)
                write_memory_file(archive_path, meta, mem["content"])
                result["archived"] += 1
            else:
                # 直接删除
                path.unlink()
                result["deleted"] += 1

        except Exception as e:
            print(f"[memory_store] 清理记忆失败 {memory_type}: {e}")
            result["skipped"] += 1

    return result


# ── 记忆标签系统 ──────────────────────────────────────────────

def add_memory_tags(student_id: str, memory_type: str, tags: List[str]) -> None:
    """给记忆添加标签"""
    mem = read_student_memory(student_id, memory_type)
    if not mem:
        return

    meta = mem["meta"]
    existing_tags = meta.get("tags", "").split(",") if meta.get("tags") else []
    existing_tags = [t.strip() for t in existing_tags if t.strip()]

    # 添加新标签，去重
    for tag in tags:
        tag = tag.strip()
        if tag and tag not in existing_tags:
            existing_tags.append(tag)

    meta["tags"] = ",".join(existing_tags)
    meta["updated_at"] = str(date.today())
    path = get_memory_path(student_id, memory_type)
    write_memory_file(path, meta, mem["content"])


def get_memories_by_tag(student_id: str, tag: str) -> List[Dict[str, Any]]:
    """按标签获取记忆"""
    results = []
    for memory_type in _MEMORY_FILENAMES.keys():
        mem = read_student_memory(student_id, memory_type)
        if not mem:
            continue
        tags = mem["meta"].get("tags", "").split(",")
        if tag in [t.strip() for t in tags]:
            mem["type"] = memory_type
            results.append(mem)
    return results


# ── 记忆删除/重置 ──────────────────────────────────────────────

def delete_student_memory(student_id: str, memory_type: str, archive: bool = True) -> bool:
    """
    删除学生某个类型的记忆。

    Args:
        student_id:    学生 open_id
        memory_type:   记忆类型
        archive:       是否先归档再删除（默认 True）

    Returns:
        是否成功
    """
    path = get_memory_path(student_id, memory_type)
    if not path.exists():
        return False

    if archive:
        try:
            # 归档到 archive 目录
            archive_dir = MEMORY_ROOT / "archive" / student_id
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / f"{Path(path).name}.{date.today()}.archived"
            path.rename(archive_path)
        except Exception as e:
            print(f"[memory_store] 归档失败，直接删除: {e}")
            try:
                path.unlink()
            except Exception:
                return False
    else:
        try:
            path.unlink()
        except Exception:
            return False

    # 更新全局索引
    try:
        update_global_index()
    except Exception:
        pass

    return True


def reset_student_memory(student_id: str, memory_type: str, student_name: str = "") -> bool:
    """
    重置学生某个类型的记忆（删除后重新初始化）。

    Args:
        student_id:    学生 open_id
        memory_type:   记忆类型
        student_name:  学生名称（可选）

    Returns:
        是否成功
    """
    # 先备份
    deleted = delete_student_memory(student_id, memory_type, archive=True)
    if not deleted:
        return False

    # 根据类型重新初始化
    initial_content = ""
    if memory_type == "student_profile":
        initial_content = "## 学生画像\n\n> 记录学生的学习特点、性格、偏好等。"
    elif memory_type == "teaching_feedback":
        initial_content = "## 教学洞察\n\n> 记录答题规律、纠错模式、进步信号等关键教学观察。"
    elif memory_type == "learning_progress":
        initial_content = "## 学习进展\n\n> 记录章节攻克情况、阶段目标、里程碑事件。"

    if initial_content:
        write_student_memory(student_id, memory_type, initial_content, student_name=student_name)

    return True


# ── 记忆统计信息 ──────────────────────────────────────────────

def get_memory_stats(student_id: str) -> Dict[str, Any]:
    """获取学生记忆统计信息"""
    stats = {
        "total_memories": 0,
        "memory_types": {},
        "total_size_bytes": 0,
        "last_updated": None,
        "importance_scores": {},
    }

    for memory_type in _MEMORY_FILENAMES.keys():
        mem = read_student_memory(student_id, memory_type)
        if not mem:
            continue

        stats["total_memories"] += 1
        stats["memory_types"][memory_type] = True

        path = get_memory_path(student_id, memory_type)
        if path.exists():
            stats["total_size_bytes"] += path.stat().st_size

        updated_at = mem["meta"].get("updated_at")
        if updated_at:
            if not stats["last_updated"] or updated_at > stats["last_updated"]:
                stats["last_updated"] = updated_at

        stats["importance_scores"][memory_type] = score_memory_importance(student_id, memory_type)

    return stats


# ── CLI 测试入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    ensure_memory_dirs()
    print(f"[memory_store] 记忆目录已初始化: {MEMORY_ROOT}")

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "test-write" and len(sys.argv) > 2:
            sid = sys.argv[2]
            append_teaching_feedback(sid, "**测试洞察**：CLI 写入验证正常。", "测试学生")
            update_learning_progress(sid, "**测试进展**：CLI 写入验证正常。", "测试学生")
            update_global_index()
            print(f"[OK] 已写入学生 {sid} 的测试记忆，并更新全局索引。")
            mems = list_student_memories(sid)
            print(f"[OK] 读取验证：{len(mems)} 条记忆文件")
            for m in mems:
                print(f"     - {m['type']}: {m['meta'].get('name', '?')}")

        elif cmd == "summary" and len(sys.argv) > 2:
            sid = sys.argv[2]
            print(get_memory_summary(sid))

        elif cmd == "search" and len(sys.argv) > 3:
            sid = sys.argv[2]
            keyword = sys.argv[3]
            results = search_memories(sid, keyword)
            print(f"[search] 找到 {len(results)} 个记忆包含 '{keyword}'")
            for r in results:
                print(f"  - {r['type']}: {len(r['matches'])} 个匹配")

        elif cmd == "stats" and len(sys.argv) > 2:
            sid = sys.argv[2]
            stats = get_memory_stats(sid)
            print(f"[stats] 记忆统计 for {sid}:")
            print(f"  总记忆数: {stats['total_memories']}")
            print(f"  总大小: {stats['total_size_bytes']} 字节")
            print(f"  最后更新: {stats['last_updated']}")
            for mem_type, score in stats["importance_scores"].items():
                print(f"  {mem_type}: 重要性 {score:.2f}")

        elif cmd == "consolidate" and len(sys.argv) > 2:
            sid = sys.argv[2]
            print(f"[consolidate] 正在整合 {sid} 的记忆...")
            result = consolidate_memories(sid)
            if result:
                print(f"[OK] 整合完成，内容长度: {len(result)}")
            else:
                print(f"[skip] 没有可整合的记忆或 API 不可用")

        elif cmd == "cleanup" and len(sys.argv) > 2:
            sid = sys.argv[2]
            days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
            print(f"[cleanup] 正在清理 {sid} {days} 天前的记忆...")
            result = cleanup_old_memories(sid, days=days)
            print(f"[OK] 归档: {result['archived']}, 删除: {result['deleted']}, 跳过: {result['skipped']}")

        elif cmd == "tags" and len(sys.argv) > 4:
            sid = sys.argv[2]
            mem_type = sys.argv[3]
            tags = sys.argv[4].split(",")
            add_memory_tags(sid, mem_type, tags)
            print(f"[OK] 已添加标签到 {sid} 的 {mem_type}: {tags}")

        elif cmd == "update-index":
            print(f"[update-index] 正在更新全局索引...")
            update_global_index()
            print(f"[OK] 索引已更新: {MEMORY_INDEX}")

        elif cmd == "delete" and len(sys.argv) > 3:
            sid = sys.argv[2]
            mem_type = sys.argv[3]
            no_archive = len(sys.argv) > 4 and sys.argv[4] == "--no-archive"
            print(f"[delete] 正在删除 {sid} 的 {mem_type}...")
            result = delete_student_memory(sid, mem_type, archive=not no_archive)
            if result:
                print(f"[OK] 删除成功")
            else:
                print(f"[skip] 记忆不存在或删除失败")

        elif cmd == "reset" and len(sys.argv) > 3:
            sid = sys.argv[2]
            mem_type = sys.argv[3]
            student_name = sys.argv[4] if len(sys.argv) > 4 else ""
            print(f"[reset] 正在重置 {sid} 的 {mem_type}...")
            result = reset_student_memory(sid, mem_type, student_name=student_name)
            if result:
                print(f"[OK] 重置成功，已备份到 archive 目录")
            else:
                print(f"[skip] 记忆不存在或重置失败")

        else:
            print("""
用法:
  python memory_store.py test-write <student_id>     - 写入测试记忆
  python memory_store.py summary <student_id>        - 显示记忆摘要
  python memory_store.py search <student_id> <keyword> - 搜索记忆
  python memory_store.py stats <student_id>          - 显示记忆统计
  python memory_store.py consolidate <student_id>    - 整合记忆
  python memory_store.py cleanup <student_id> [days] - 清理过期记忆
  python memory_store.py tags <student_id> <type> <tag1,tag2> - 添加标签
  python memory_store.py update-index                - 更新全局索引
  python memory_store.py delete <student_id> <type> [--no-archive] - 删除记忆（默认归档）
  python memory_store.py reset <student_id> <type> [name] - 重置记忆（归档后重新初始化）
""".strip())

    else:
        print("""
用法:
  python memory_store.py test-write <student_id>     - 写入测试记忆
  python memory_store.py summary <student_id>        - 显示记忆摘要
  python memory_store.py search <student_id> <keyword> - 搜索记忆
  python memory_store.py stats <student_id>          - 显示记忆统计
  python memory_store.py consolidate <student_id>    - 整合记忆
  python memory_store.py cleanup <student_id> [days] - 清理过期记忆
  python memory_store.py tags <student_id> <type> <tag1,tag2> - 添加标签
  python memory_store.py update-index                - 更新全局索引
  python memory_store.py delete <student_id> <type> [--no-archive] - 删除记忆（默认归档）
  python memory_store.py reset <student_id> <type> [name] - 重置记忆（归档后重新初始化）
""".strip())
