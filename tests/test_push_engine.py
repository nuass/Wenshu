"""
测试 push_engine.py 核心逻辑
运行：cd auto_send && python -m pytest tests/ -v
"""

import json
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from push_engine import (
    _recent_sent_ids,
    _ever_sent_wrong_ids,
    score_question,
    select_questions,
    record_push,
)


# ── fixtures ──────────────────────────────────────────────────

def make_student(
    difficulty=3,
    topic_mastery=None,
    weak_topics=None,
    send_history=None,
):
    return {
        "student_id": "test_stu",
        "current_difficulty": difficulty,
        "topic_mastery": topic_mastery or {},
        "weak_topics": weak_topics or [],
        "send_history": send_history or [],
    }


def make_question(id, difficulty=3, tags=None):
    return {
        "id": id,
        "difficulty": difficulty,
        "topic_tags": tags or [],
        "correct_answer": "A",
        "question_image": f"output/images/q{id}.png",
    }


def sent_record(question_id, days_ago=0, is_correct=None, answered=False):
    d = str(date.today() - timedelta(days=days_ago))
    return {
        "question_id": question_id,
        "sent_at": d,
        "answered": answered or (is_correct is not None),
        "is_correct": is_correct,
        "submitted_answer": "A" if is_correct is not None else None,
    }


# ── _recent_sent_ids ──────────────────────────────────────────

def test_recent_sent_ids_within_window():
    history = [sent_record(1, days_ago=3), sent_record(2, days_ago=6)]
    assert _recent_sent_ids(history) == {1, 2}


def test_recent_sent_ids_outside_window():
    history = [sent_record(1, days_ago=7), sent_record(2, days_ago=10)]
    assert _recent_sent_ids(history) == set()


def test_recent_sent_ids_mixed():
    history = [sent_record(1, days_ago=3), sent_record(2, days_ago=8)]
    assert _recent_sent_ids(history) == {1}


# ── _ever_sent_wrong_ids ──────────────────────────────────────

def test_ever_sent_wrong_ids():
    history = [
        sent_record(1, is_correct=False),
        sent_record(2, is_correct=True),
        sent_record(3, is_correct=False),
    ]
    assert _ever_sent_wrong_ids(history) == {1, 3}


def test_ever_sent_wrong_ids_unanswered():
    history = [sent_record(1)]  # answered=False
    assert _ever_sent_wrong_ids(history) == set()


# ── score_question ────────────────────────────────────────────

def test_score_difficulty_match():
    student = make_student(difficulty=3)
    q_match = make_question(1, difficulty=3)
    q_far   = make_question(2, difficulty=1)
    assert score_question(q_match, student) > score_question(q_far, student)


def test_score_weak_topic_boost():
    student = make_student(
        topic_mastery={"假设检验": 0.3},
        weak_topics=["假设检验"],
    )
    q_weak  = make_question(1, tags=["假设检验"])
    q_other = make_question(2, tags=["均值"])
    assert score_question(q_weak, student) > score_question(q_other, student)


def test_score_no_tags():
    student = make_student()
    q = make_question(1, tags=[])
    s = score_question(q, student)
    assert 0.0 <= s <= 1.0


# ── select_questions ──────────────────────────────────────────

def test_select_returns_push_count():
    from config import PUSH_COUNT
    student = make_student()
    questions = [make_question(i) for i in range(1, 10)]
    selected = select_questions(student, questions)
    assert len(selected) == PUSH_COUNT


def test_select_excludes_recent():
    student = make_student(send_history=[
        sent_record(1, days_ago=1),
        sent_record(2, days_ago=1),
        sent_record(3, days_ago=1),
    ])
    questions = [make_question(i) for i in range(1, 8)]
    selected = select_questions(student, questions)
    selected_ids = {q["id"] for q in selected}
    assert not selected_ids & {1, 2, 3}


def test_select_fills_with_wrong_when_fresh_insufficient():
    # q1 超出去重窗口且答错过，可二刷；q2/q3 近期推过；q4 是新题
    # 新题只有 q4 一道，不足 PUSH_COUNT=3，q1 应被二刷补充
    student = make_student(send_history=[
        sent_record(1, days_ago=8, is_correct=False),  # 超出去重窗口且答错
        sent_record(2, days_ago=1),
        sent_record(3, days_ago=1),
    ])
    questions = [make_question(i) for i in range(1, 5)]
    selected = select_questions(student, questions)
    selected_ids = {q["id"] for q in selected}
    assert 1 in selected_ids  # 答错题超出窗口，应被二刷补充


def test_select_empty_when_all_recent():
    from config import PUSH_COUNT, DEDUP_DAYS
    student = make_student(send_history=[
        sent_record(i, days_ago=1) for i in range(1, 4)
    ])
    questions = [make_question(i) for i in range(1, 4)]
    selected = select_questions(student, questions)
    assert selected == []


# ── record_push ───────────────────────────────────────────────

def test_record_push_appends_history():
    student = make_student()
    questions = [make_question(1), make_question(2)]
    result = record_push(student, questions)

    assert result["student_id"] == "test_stu"
    assert len(result["questions"]) == 2
    assert len(student["send_history"]) == 2
    assert all(not r["answered"] for r in student["send_history"])


def test_record_push_result_fields():
    student = make_student()
    q = make_question(5, difficulty=4, tags=["p值"])
    result = record_push(student, [q])

    pushed = result["questions"][0]
    assert pushed["question_id"] == 5
    assert pushed["difficulty"] == 4
    assert "p值" in pushed["topic_tags"]
