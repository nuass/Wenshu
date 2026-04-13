import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

PUSH_LOG = LOG_DIR / "push_events.jsonl"
ANSWER_LOG = LOG_DIR / "answer_events.jsonl"
GRADING_LOG = LOG_DIR / "grading_events.jsonl"


def _generate_record_id() -> str:
    """生成唯一的记录ID"""
    return f"rec_{uuid.uuid4().hex[:12]}"


def _write_log(log_file: Path, event: Dict[str, Any]) -> None:
    """写入日志事件到JSONL文件"""
    event["timestamp"] = datetime.now().isoformat()
    if "record_id" not in event:
        event["record_id"] = _generate_record_id()
    with open(log_file, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_push_event(
    student_id: str,
    question_ids: List[int],
    difficulty: int,
    weak_topics: Optional[List[str]] = None,
    chat_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    push_reason: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """记录题目推送事件

    Args:
        push_reason: 推送原因 (daily_push/weak_topic/wrong_review/manual)
        message_id: 飞书消息ID

    Returns:
        record_id: 记录ID
    """
    record_id = _generate_record_id()
    event = {
        "record_id": record_id,
        "event_type": "push",
        "student_id": student_id,
        "chat_id": chat_id,
        "teacher_id": teacher_id,
        "question_ids": question_ids,
        "difficulty": difficulty,
        "weak_topics": weak_topics or [],
        "push_reason": push_reason or "daily_push",
        "message_id": message_id,
    }
    _write_log(PUSH_LOG, event)
    return record_id


def log_answer_event(
    student_id: str,
    raw_text: str,
    parsed_answers: Dict[int, str],
    chat_id: Optional[str] = None,
    response_time_seconds: Optional[int] = None,
    message_id: Optional[str] = None,
) -> str:
    """记录学生答题事件

    Args:
        response_time_seconds: 答题耗时（秒）
        message_id: 飞书消息ID

    Returns:
        record_id: 记录ID
    """
    record_id = _generate_record_id()
    event = {
        "record_id": record_id,
        "event_type": "answer",
        "student_id": student_id,
        "chat_id": chat_id,
        "raw_text": raw_text,
        "parsed_answers": parsed_answers,
        "response_time_seconds": response_time_seconds,
        "message_id": message_id,
    }
    _write_log(ANSWER_LOG, event)
    return record_id


def log_grading_event(
    student_id: str,
    question_id: int,
    student_answer: str,
    correct_answer: str,
    is_correct: bool,
    topic: Optional[str] = None,
    mastery_before: Optional[float] = None,
    mastery_after: Optional[float] = None,
    difficulty_before: Optional[int] = None,
    difficulty_after: Optional[int] = None,
    chat_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """记录答题评判事件

    Args:
        message_id: 飞书消息ID

    Returns:
        record_id: 记录ID
    """
    record_id = _generate_record_id()
    event = {
        "record_id": record_id,
        "event_type": "grading",
        "student_id": student_id,
        "chat_id": chat_id,
        "teacher_id": teacher_id,
        "question_id": question_id,
        "student_answer": student_answer,
        "correct_answer": correct_answer,
        "is_correct": is_correct,
        "topic": topic,
        "mastery_before": mastery_before,
        "mastery_after": mastery_after,
        "difficulty_before": difficulty_before,
        "difficulty_after": difficulty_after,
        "message_id": message_id,
    }
    _write_log(GRADING_LOG, event)
    return record_id


def query_student_history(student_id: str, log_type: str = "all") -> List[Dict]:
    """查询学生的历史记录"""
    results = []

    log_files = []
    if log_type in ("all", "push"):
        log_files.append(PUSH_LOG)
    if log_type in ("all", "answer"):
        log_files.append(ANSWER_LOG)
    if log_type in ("all", "grading"):
        log_files.append(GRADING_LOG)

    for log_file in log_files:
        if not log_file.exists():
            continue
        with open(log_file, "r") as f:
            for line in f:
                event = json.loads(line)
                if event.get("student_id") == student_id:
                    results.append(event)

    return sorted(results, key=lambda x: x.get("timestamp", ""))


def query_question_history(question_id: int) -> List[Dict]:
    """查询题目的历史记录"""
    results = []

    if GRADING_LOG.exists():
        with open(GRADING_LOG, "r") as f:
            for line in f:
                event = json.loads(line)
                if event.get("question_id") == question_id:
                    results.append(event)

    return sorted(results, key=lambda x: x.get("timestamp", ""))


# ── CLI 入口（原 query_logs.py）────────────────────────────────

def _print_student_history(student_id: str, log_type: str = "all") -> None:
    records = query_student_history(student_id, log_type)
    if not records:
        print(f"未找到学生 {student_id} 的日志记录")
        return
    print(f"\n{'='*80}")
    print(f"学生 {student_id} 的学习日志 (共 {len(records)} 条)")
    print(f"{'='*80}\n")
    for i, record in enumerate(records, 1):
        timestamp  = record.get("timestamp", "")
        event_type = record.get("event_type", "")
        print(f"[{i}] {timestamp} | {event_type.upper()}")
        if event_type == "push":
            print(f"    推送题目: {record.get('question_ids')}")
            print(f"    难度: {record.get('difficulty')} | 薄弱点: {record.get('weak_topics')}")
            print(f"    推送原因: {record.get('push_reason')}")
        elif event_type == "answer":
            print(f"    原始答案: {record.get('raw_text')}")
            print(f"    解析结果: {record.get('parsed_answers')}")
        elif event_type == "grading":
            is_correct = "✅" if record.get("is_correct") else "❌"
            print(f"    {is_correct} 题目 {record.get('question_id')}: "
                  f"{record.get('student_answer')} (正确答案: {record.get('correct_answer')})")
            print(f"    知识点: {record.get('topic')}")
            mb, ma = record.get("mastery_before"), record.get("mastery_after")
            if mb is not None and ma is not None:
                print(f"    掌握度: {mb:.1%} → {ma:.1%}")
            db, da = record.get("difficulty_before"), record.get("difficulty_after")
            if db != da:
                print(f"    难度调整: {db} → {da}")
        print()


def _print_question_history(question_id: int) -> None:
    records = query_question_history(question_id)
    if not records:
        print(f"未找到题目 {question_id} 的日志记录")
        return
    print(f"\n{'='*80}")
    print(f"题目 {question_id} 的答题历史 (共 {len(records)} 条)")
    print(f"{'='*80}\n")
    correct_count = sum(1 for r in records if r.get("is_correct"))
    print(f"正确率: {correct_count}/{len(records)} ({correct_count/len(records):.1%})\n")
    for i, record in enumerate(records, 1):
        is_correct = "✅" if record.get("is_correct") else "❌"
        print(f"[{i}] {record.get('timestamp', '')} | {record.get('student_id', '')}")
        print(f"    {is_correct} 学生答案: {record.get('student_answer')} "
              f"(正确答案: {record.get('correct_answer')})")
        print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="日志查询工具")
    parser.add_argument("--student",  help="学生 ID")
    parser.add_argument("--question", type=int, help="题目 ID")
    parser.add_argument("--type", default="all",
                        choices=["all", "push", "answer", "grading"], help="日志类型")
    args = parser.parse_args()

    if args.student:
        _print_student_history(args.student, args.type)
    elif args.question:
        _print_question_history(args.question)
    else:
        parser.print_help()
