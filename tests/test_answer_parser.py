"""
测试 answer_parser.py 核心逻辑
"""

import json
import os
import pytest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from answer_parser import extract_raw_answers, parse_answers


# ── extract_raw_answers ───────────────────────────────────────

def test_single_letter():
    assert extract_raw_answers("A") == [(None, "A")]

def test_single_letter_lowercase():
    assert extract_raw_answers("b") == [(None, "B")]

def test_verb_prefix():
    assert extract_raw_answers("选C") == [(None, "C")]
    assert extract_raw_answers("答案是D") == [(None, "D")]

def test_colloquial():
    assert extract_raw_answers("A吧") == [(None, "A")]
    assert extract_raw_answers("应该是B") == [(None, "B")]

def test_numbered_chinese():
    result = extract_raw_answers("第1题A，第2题B，第3题C")
    assert result == [(1, "A"), (2, "B"), (3, "C")]

def test_numbered_dot():
    result = extract_raw_answers("1.A 2.B 3.C")
    assert result == [(1, "A"), (2, "B"), (3, "C")]

def test_numbered_circle():
    result = extract_raw_answers("①A ②B ③C")
    assert result == [(1, "A"), (2, "B"), (3, "C")]

def test_multiple_no_number():
    result = extract_raw_answers("选A，选B，选C")
    answers = [a for _, a in result]
    assert answers == ["A", "B", "C"]

def test_no_answer():
    assert extract_raw_answers("今天天气不错") == []

def test_empty_string():
    assert extract_raw_answers("") == []


# ── parse_answers ─────────────────────────────────────────────

MOCK_CONTEXT = {
    "last_pushed_question_ids": [10, 20, 30]
}

def mock_load_context(student_id):
    return MOCK_CONTEXT


def test_parse_single_answer():
    with patch("answer_parser.load_context", mock_load_context):
        result = parse_answers("stu_001", "A")
    assert len(result) == 1
    assert result[0].question_id == 10
    assert result[0].answer == "A"


def test_parse_three_answers_no_number():
    with patch("answer_parser.load_context", mock_load_context):
        result = parse_answers("stu_001", "选A，选B，选C")
    assert [(r.question_id, r.answer) for r in result] == [(10, "A"), (20, "B"), (30, "C")]


def test_parse_numbered_answers():
    with patch("answer_parser.load_context", mock_load_context):
        result = parse_answers("stu_001", "第1题C，第2题A，第3题B")
    assert [(r.question_id, r.answer) for r in result] == [(10, "C"), (20, "A"), (30, "B")]


def test_parse_dedup_same_question():
    with patch("answer_parser.load_context", mock_load_context):
        result = parse_answers("stu_001", "第1题A，第1题B")
    # 同一题只保留第一次
    assert len(result) == 1
    assert result[0].answer == "A"


def test_parse_no_context():
    # 上下文缺失且无历史记录时，应返回空列表
    with patch("answer_parser.load_context", lambda _: {}):
        with patch("answer_parser._fallback_ids_from_profile", lambda _: []):
            result = parse_answers("stu_001", "A")
    assert result == []


def test_parse_excess_answers_truncated():
    # 推送了3题，提交4个答案，第4个丢弃
    with patch("answer_parser.load_context", mock_load_context):
        result = parse_answers("stu_001", "选A，选B，选C，选D")
    assert len(result) == 3
