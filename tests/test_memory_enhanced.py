#!/usr/bin/env python3
"""
测试增强后的记忆系统功能。
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_store import (
    ensure_memory_dirs,
    append_teaching_feedback,
    update_learning_progress,
    search_memories,
    score_memory_importance,
    get_memory_stats,
    add_memory_tags,
    get_memories_by_tag,
    list_student_memories,
)


def test_search():
    """测试记忆搜索功能"""
    print("=" * 60)
    print("测试 1: 记忆搜索功能")
    print("=" * 60)

    # 使用一个已存在的学生
    test_sid = "ou_b331f3918e6e9135a4edb24976198248"

    results = search_memories(test_sid, "箱线图")
    print(f"搜索 '箱线图': 找到 {len(results)} 个记忆")
    for r in results:
        print(f"  - {r['type']}: {len(r['matches'])} 个匹配")

    print()
    return len(results) > 0


def test_stats():
    """测试记忆统计功能"""
    print("=" * 60)
    print("测试 2: 记忆统计功能")
    print("=" * 60)

    test_sid = "ou_b331f3918e6e9135a4edb24976198248"
    stats = get_memory_stats(test_sid)

    print(f"总记忆数: {stats['total_memories']}")
    print(f"总大小: {stats['total_size_bytes']} 字节")
    print(f"最后更新: {stats['last_updated']}")
    print(f"重要性评分:")
    for mem_type, score in stats["importance_scores"].items():
        print(f"  {mem_type}: {score:.2f}")

    print()
    return stats["total_memories"] > 0


def test_importance_scoring():
    """测试记忆重要性评分"""
    print("=" * 60)
    print("测试 3: 重要性评分功能")
    print("=" * 60)

    test_sid = "ou_b331f3918e6e9135a4edb24976198248"

    for mem_type in ["teaching_feedback", "learning_progress"]:
        score = score_memory_importance(test_sid, mem_type)
        print(f"  {mem_type}: {score:.2f}")

    print()
    return True


def test_tags():
    """测试标签功能"""
    print("=" * 60)
    print("测试 4: 标签功能")
    print("=" * 60)

    test_sid = "ou_b331f3918e6e9135a4edb24976198248"

    # 添加标签
    add_memory_tags(test_sid, "teaching_feedback", ["AP统计", "测试标签"])
    print("已添加标签: ['AP统计', '测试标签']")

    # 按标签查询
    results = get_memories_by_tag(test_sid, "AP统计")
    print(f"按 'AP统计' 标签查询: 找到 {len(results)} 个记忆")

    print()
    return len(results) > 0


def run_all_tests():
    """运行所有测试"""
    print()
    print("=" * 60)
    print("记忆系统增强功能测试")
    print("=" * 60)
    print()

    ensure_memory_dirs()

    tests = [
        ("搜索功能", test_search),
        ("统计功能", test_stats),
        ("重要性评分", test_importance_scoring),
        ("标签功能", test_tags),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            result = test_func()
            if result:
                print(f"✅ {name}: 通过")
                passed += 1
            else:
                print(f"❌ {name}: 失败")
                failed += 1
        except Exception as e:
            print(f"❌ {name}: 异常 - {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
