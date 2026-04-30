#!/usr/bin/env python3
"""
测试统一记忆层（memory.py）的功能。
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory import (
    short_term_memory,
    get_full_context,
    # 长记忆功能
    get_memory_stats,
    search_memories,
    add_memory_tags,
    append_teaching_feedback,
    update_learning_progress,
)


def test_short_term_memory():
    """测试短记忆功能"""
    print("=" * 60)
    print("测试 1: 短记忆功能")
    print("=" * 60)

    test_sid = "test_unified_memory_001"

    # 测试写入
    short_term_memory.append_message(test_sid, "student", "我想做练习")
    short_term_memory.append_message(test_sid, "bot", "好的，这是题目")
    short_term_memory.set_last_pushed_ids(test_sid, [201, 202, 203])
    short_term_memory.set(test_sid, "custom_key", "custom_value")
    print("✅ 短记忆写入完成")

    # 测试读取
    state = short_term_memory.get_state(test_sid)
    messages = short_term_memory.get_recent_messages(test_sid)
    ids = short_term_memory.get_last_pushed_ids(test_sid)
    custom_val = short_term_memory.get(test_sid, "custom_key")

    print(f"  状态键数: {len(state)}")
    print(f"  消息数: {len(messages)}")
    print(f"  推送IDs: {ids}")
    print(f"  自定义值: {custom_val}")

    # 清理
    short_term_memory.clear(test_sid)
    print("✅ 短记忆清理完成")

    print()
    return True


def test_full_context():
    """测试完整上下文获取"""
    print("=" * 60)
    print("测试 2: 完整上下文获取")
    print("=" * 60)

    test_sid = "ou_b331f3918e6e9135a4edb24976198248"

    # 先写点短记忆
    short_term_memory.append_message(test_sid, "student", "这道题怎么做？")
    short_term_memory.append_message(test_sid, "bot", "让我看看解析...")

    ctx = get_full_context(test_sid)

    print(f"短记忆状态键数: {len(ctx['short_term'])}")
    print(f"最近消息数: {len(ctx['recent_messages'])}")
    print(f"长记忆摘要长度: {len(ctx['long_term_summary'])} 字符")

    if ctx['long_term_summary']:
        preview = ctx['long_term_summary'][:100] + "..."
        print(f"长记忆摘要预览: {preview}")

    # 清理测试消息
    short_term_memory.clear_messages(test_sid)

    print()
    return True


def test_long_memory_integration():
    """测试长记忆功能集成"""
    print("=" * 60)
    print("测试 3: 长记忆功能集成")
    print("=" * 60)

    test_sid = "ou_b331f3918e6e9135a4edb24976198248"

    # 测试统计（从 memory.py 导入）
    stats = get_memory_stats(test_sid)
    print(f"总记忆数: {stats['total_memories']}")

    # 测试搜索（从 memory.py 导入）
    results = search_memories(test_sid, "箱线图")
    print(f"搜索'箱线图': 找到 {len(results)} 个记忆")

    print()
    return True


def run_all_tests():
    """运行所有测试"""
    print()
    print("=" * 60)
    print("统一记忆层功能测试")
    print("=" * 60)
    print()

    tests = [
        ("短记忆功能", test_short_term_memory),
        ("完整上下文", test_full_context),
        ("长记忆集成", test_long_memory_integration),
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
