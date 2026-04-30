#!/usr/bin/env python3
"""
memory.py

Wenshu 飞书推题系统 — 统一记忆层（长短记忆分离）

架构：
    ┌─────────────────────────────────────────────────────────┐
    │                     Memory Layer                         │
    ├──────────────────────────┬──────────────────────────────┤
    │   短记忆 (Short-Term)    │   长记忆 (Long-Term)         │
    │   不分角色, 会话级       │   按学生分角色, 持久化       │
    ├──────────────────────────┼──────────────────────────────┤
    │ • 当前对话上下文         │ • 学生画像 (profile)         │
    │ • 最近交互状态           │ • 教学反馈 (feedback)        │
    │ • 临时缓存               │ • 学习进展 (progress)        │
    └──────────────────────────┴──────────────────────────────┘

导出接口：
    # 短记忆（会话级）
    short_term = ShortTermMemory()
    short_term.set(student_id, key, value)
    short_term.get(student_id, key)
    short_term.append_message(student_id, role, content)
    short_term.get_recent_messages(student_id, limit=10)
    short_term.clear(student_id)

    # 长记忆（持久化，复用 memory_store）
    from memory_store import (
        write_student_memory,
        read_student_memory,
        append_teaching_feedback,
        update_learning_progress,
        get_memory_summary,
    )
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from threading import RLock

from config import STUDENTS_DIR

# 复用现有的长记忆模块
from memory_store import (
    ensure_memory_dirs,
    write_memory_file,
    read_memory_file,
    get_memory_path,
    get_teacher_memory_path,
    write_student_memory,
    read_student_memory,
    list_student_memories,
    append_teaching_feedback,
    update_learning_progress,
    get_memory_summary,
    update_global_index,
    MEMORY_INDEX,
    # 增强功能 ✨
    search_memories,
    score_memory_importance,
    cleanup_old_memories,
    consolidate_memories,
    add_memory_tags,
    get_memories_by_tag,
    get_memory_stats,
)

__all__ = [
    # 短记忆
    "ShortTermMemory",
    "short_term_memory",
    # 长记忆（重导出）
    "ensure_memory_dirs",
    "write_memory_file",
    "read_memory_file",
    "write_student_memory",
    "read_student_memory",
    "list_student_memories",
    "append_teaching_feedback",
    "update_learning_progress",
    "get_memory_summary",
    "update_global_index",
    # 增强功能 ✨
    "search_memories",
    "score_memory_importance",
    "cleanup_old_memories",
    "consolidate_memories",
    "add_memory_tags",
    "get_memories_by_tag",
    "get_memory_stats",
    # 统一上下文
    "get_full_context",
]


# ── 短记忆（内存 + 文件双写，会话级）───────────────────────────

class ShortTermMemory:
    """
    短记忆管理器：
    - 不分角色（但按 student_id 隔离数据）
    - 内存缓存 + 可选持久化（用于重启恢复）
    - 保存当前对话、临时状态、最近交互
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._lock = RLock()
        self._cache: dict[str, dict[str, Any]] = {}  # {student_id: {key: value}}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        """从磁盘加载持久化的短记忆（用于重启恢复）"""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                data = json.load(f)
                for sid, state in data.items():
                    self._cache[sid] = state
        except Exception:
            pass

    def _save_to_disk(self) -> None:
        """持久化短记忆到磁盘（可选）"""
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._persist_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            tmp.replace(self._persist_path)
        except Exception:
            tmp.unlink(missing_ok=True)

    def set(self, student_id: str, key: str, value: Any) -> None:
        """设置短记忆键值"""
        with self._lock:
            if student_id not in self._cache:
                self._cache[student_id] = {}
            self._cache[student_id][key] = value
            self._save_to_disk()

    def get(self, student_id: str, key: str, default: Any = None) -> Any:
        """获取短记忆值"""
        with self._lock:
            return self._cache.get(student_id, {}).get(key, default)

    def delete(self, student_id: str, key: str) -> None:
        """删除短记忆键"""
        with self._lock:
            if student_id in self._cache:
                self._cache[student_id].pop(key, None)
                self._save_to_disk()

    def clear(self, student_id: str) -> None:
        """清空学生的所有短记忆"""
        with self._lock:
            self._cache.pop(student_id, None)
            self._save_to_disk()

    def get_state(self, student_id: str) -> dict[str, Any]:
        """获取学生的完整短记忆状态"""
        with self._lock:
            return dict(self._cache.get(student_id, {}))

    # ── 对话历史快捷方法 ─────────────────────────────────────

    def append_message(
        self,
        student_id: str,
        role: str,
        content: str,
        max_limit: int = 20,
    ) -> None:
        """
        追加一条对话消息到短记忆
        role: "student" | "bot"
        """
        with self._lock:
            if student_id not in self._cache:
                self._cache[student_id] = {}
            messages = self._cache[student_id].setdefault("messages", [])
            messages.append({
                "role": role,
                "content": content,
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
            # 只保留最近 max_limit 条
            if len(messages) > max_limit:
                self._cache[student_id]["messages"] = messages[-max_limit:]
            self._save_to_disk()

    def get_recent_messages(
        self,
        student_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """获取最近的对话消息"""
        with self._lock:
            messages = self._cache.get(student_id, {}).get("messages", [])
            return list(messages[-limit:])

    def clear_messages(self, student_id: str) -> None:
        """清空对话历史"""
        with self._lock:
            if student_id in self._cache:
                self._cache[student_id].pop("messages", None)
                self._save_to_disk()

    # ── 推题状态快捷方法 ─────────────────────────────────────

    def set_last_pushed_ids(self, student_id: str, question_ids: list[int]) -> None:
        """设置最近推送的题目ID列表"""
        self.set(student_id, "last_pushed_ids", question_ids)

    def get_last_pushed_ids(self, student_id: str) -> list[int]:
        """获取最近推送的题目ID列表"""
        return self.get(student_id, "last_pushed_ids", [])

    def set_current_session_id(self, student_id: str, session_id: str) -> None:
        """设置当前会话ID"""
        self.set(student_id, "current_session_id", session_id)

    def get_current_session_id(self, student_id: str) -> Optional[str]:
        """获取当前会话ID"""
        return self.get(student_id, "current_session_id")


# 全局短记忆单例（可选持久化到 students/short_term.json）
_short_term_path = Path(STUDENTS_DIR) / "short_term.json"
short_term_memory = ShortTermMemory(persist_path=str(_short_term_path))


# ── 统一上下文获取 ─────────────────────────────────────────────

def get_full_context(student_id: str) -> dict:
    """
    获取学生的完整上下文（短记忆 + 长记忆摘要）
    供 intent_router / push_engine 使用
    """
    # 短记忆
    short_state = short_term_memory.get_state(student_id)
    recent_messages = short_term_memory.get_recent_messages(student_id, limit=10)

    # 长记忆摘要
    try:
        long_summary = get_memory_summary(student_id)
    except Exception:
        long_summary = ""

    return {
        "short_term": short_state,
        "recent_messages": recent_messages,
        "long_term_summary": long_summary,
    }


# ── CLI 测试入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    ensure_memory_dirs()
    print(f"[memory] 统一记忆层已初始化")
    print(f"[memory] 短记忆持久化: {_short_term_path if _short_term_path.exists() else '(无)'}")

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        # ── 短记忆命令 ──────────────────────────────────────

        if cmd == "test-short" and len(sys.argv) > 2:
            sid = sys.argv[2]
            print(f"\n[测试] 短记忆写入: {sid}")
            short_term_memory.append_message(sid, "student", "你好")
            short_term_memory.append_message(sid, "bot", "你好，来做道题？")
            short_term_memory.set_last_pushed_ids(sid, [101, 102, 103])
            short_term_memory.set(sid, "temp_flag", "test_value")
            print(f"[OK] 写入完成")

            print(f"\n[测试] 短记忆读取:")
            state = short_term_memory.get_state(sid)
            print(f"  完整状态: {json.dumps(state, ensure_ascii=False, indent=2)}")
            msgs = short_term_memory.get_recent_messages(sid)
            print(f"  对话数: {len(msgs)}")
            ids = short_term_memory.get_last_pushed_ids(sid)
            print(f"  推送ID: {ids}")

        elif cmd == "context" and len(sys.argv) > 2:
            sid = sys.argv[2]
            ctx = get_full_context(sid)
            print(json.dumps(ctx, ensure_ascii=False, indent=2))

        elif cmd == "clear" and len(sys.argv) > 2:
            sid = sys.argv[2]
            short_term_memory.clear(sid)
            print(f"[OK] 已清空 {sid} 的短记忆")

        # ── 长记忆命令（代理）───────────────────────────────────

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

        else:
            print("""
╔══════════════════════════════════════════════════════════════╗
║                    Wenshu 统一记忆层 - 命令行工具                     ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║ 【短记忆命令】（会话级）                                        ║
║   test-short <student_id>    - 测试短记忆功能                 ║
║   context <student_id>       - 查看完整上下文（短+长）            ║
║   clear <student_id>         - 清空短记忆                          ║
║                                                              ║
║ 【长记忆命令】（持久化）                                        ║
║   search <student_id> <keyword>   - 搜索记忆                    ║
║   stats <student_id>          - 记忆统计                          ║
║   consolidate <student_id>    - 整合记忆（LLM压缩）                 ║
║   cleanup <student_id> [days] - 清理过期记忆               ║
║   tags <student_id> <type> <tag1,tag2> - 添加标签            ║
║   update-index             - 更新全局索引                         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
            """.strip())
    else:
        print("""
╔══════════════════════════════════════════════════════════════╗
║                    Wenshu 统一记忆层 - 命令行工具                     ║
╠══════════════════════════════════════════════════════════════╣
║  运行 python memory.py <command> 查看详细用法                       ║
╚══════════════════════════════════════════════════════════════╝
        """.strip())
