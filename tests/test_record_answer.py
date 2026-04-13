"""
测试 record_answer.py 核心逻辑
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from record_answer import (
    update_answer_record,
    recalculate_topic_mastery,
    update_weak_topics,
    adapt_difficulty,
)


# ── fixtures ──────────────────────────────────────────────────

def make_student(difficulty=3, topic_mastery=None, send_history=None):
    return {
        "student_id": "test_stu",
        "current_difficulty": difficulty,
        "topic_mastery": topic_mastery or {},
        "weak_topics": [],
        "send_history": send_history or [],
    }


def unanswered(question_id):
    return {
        "question_id": question_id,
        "sent_at": "2026-01-01",
        "answered": False,
        "is_correct": None,
        "submitted_answer": None,
    }


def answered(question_id, is_correct):
    return {
        "question_id": question_id,
        "sent_at": "2026-01-01",
        "answered": True,
        "is_correct": is_correct,
        "submitted_answer": "A",
    }


# ── update_answer_record ──────────────────────────────────────

def test_update_fills_unanswered_record():
    student = make_student(send_history=[unanswered(5)])
    update_answer_record(student, 5, "B", True)
    r = student["send_history"][0]
    assert r["answered"] is True
    assert r["is_correct"] is True
    assert r["submitted_answer"] == "B"
    assert "answered_at" in r


def test_update_appends_if_no_record():
    student = make_student()
    update_answer_record(student, 99, "C", False)
    assert len(student["send_history"]) == 1
    assert student["send_history"][0]["question_id"] == 99


def test_update_uses_most_recent_unanswered():
    # 同一题推了两次，第二次未答，应更新第二条
    student = make_student(send_history=[
        answered(5, True),
        unanswered(5),
    ])
    update_answer_record(student, 5, "D", False)
    assert student["send_history"][1]["is_correct"] is False
    assert student["send_history"][0]["is_correct"] is True  # 第一条不变


# ── recalculate_topic_mastery ─────────────────────────────────

def test_recalculate_mastery_all_correct():
    student = make_student(send_history=[answered(1, True), answered(2, True)])
    questions_db = {
        1: {"id": 1, "topic_tags": ["p值"]},
        2: {"id": 2, "topic_tags": ["p值"]},
    }
    question = {"topic_tags": ["p值"]}
    recalculate_topic_mastery(student, question, questions_db)
    assert student["topic_mastery"]["p值"] == 1.0


def test_recalculate_mastery_mixed():
    student = make_student(send_history=[answered(1, True), answered(2, False)])
    questions_db = {
        1: {"id": 1, "topic_tags": ["均值"]},
        2: {"id": 2, "topic_tags": ["均值"]},
    }
    question = {"topic_tags": ["均值"]}
    recalculate_topic_mastery(student, question, questions_db)
    assert student["topic_mastery"]["均值"] == 0.5


def test_recalculate_no_tags():
    student = make_student()
    recalculate_topic_mastery(student, {"topic_tags": []}, {})
    assert student["topic_mastery"] == {}


def test_recalculate_none_question():
    student = make_student()
    recalculate_topic_mastery(student, None, {})
    assert student["topic_mastery"] == {}


# ── update_weak_topics ────────────────────────────────────────

def test_weak_topics_below_threshold():
    student = make_student(topic_mastery={"假设检验": 0.4, "p值": 0.8})
    update_weak_topics(student)
    assert student["weak_topics"] == ["假设检验"]


def test_weak_topics_all_strong():
    student = make_student(topic_mastery={"假设检验": 0.7, "p值": 0.9})
    update_weak_topics(student)
    assert student["weak_topics"] == []


def test_weak_topics_exactly_threshold():
    # 0.6 不低于阈值，不算薄弱
    student = make_student(topic_mastery={"均值": 0.6})
    update_weak_topics(student)
    assert student["weak_topics"] == []


# ── adapt_difficulty ──────────────────────────────────────────

def test_difficulty_increases_on_high_accuracy():
    history = [answered(i, True) for i in range(1, 11)]  # 10/10 正确
    student = make_student(difficulty=3, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 4


def test_difficulty_decreases_on_low_accuracy():
    history = [answered(i, i > 5) for i in range(1, 11)]  # 5/10 = 50%，< 0.5 不触发降级
    student = make_student(difficulty=3, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 3  # 50% 不触发降级

    history2 = [answered(i, i > 6) for i in range(1, 11)]  # 4/10 = 40%
    student2 = make_student(difficulty=3, send_history=history2)
    adapt_difficulty(student2)
    assert student2["current_difficulty"] == 2


def test_difficulty_no_change_on_medium_accuracy():
    history = [answered(i, i <= 7) for i in range(1, 11)]  # 7/10 = 70%
    student = make_student(difficulty=3, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 3


def test_difficulty_not_adjusted_below_10_records():
    history = [answered(i, True) for i in range(1, 9)]  # 只有 8 条
    student = make_student(difficulty=3, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 3


def test_difficulty_capped_at_max():
    history = [answered(i, True) for i in range(1, 11)]
    student = make_student(difficulty=5, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 5


def test_difficulty_capped_at_min():
    history = [answered(i, False) for i in range(1, 11)]
    student = make_student(difficulty=1, send_history=history)
    adapt_difficulty(student)
    assert student["current_difficulty"] == 1
