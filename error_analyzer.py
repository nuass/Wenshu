#!/usr/bin/env python3
"""
error_analyzer.py

对学生答错的题目，调用 Claude API 生成针对性的错误分析。

输入：题目文字、解析文字、学生选项、正确答案、学生薄弱点
输出：自然语言错误分析（2-4句，指出错误原因 + 复习建议）

使用方式：
  # 作为模块
  from error_analyzer import analyze_error

  # CLI 调试
  python error_analyzer.py <student_id> <question_id>
"""

import json
import os
import sys

from openai import OpenAI
from config import UNIAPI_KEY, UNIAPI_BASE, QUESTIONS_JSON
from student_store import load_student as _load_student

_client = OpenAI(api_key=UNIAPI_KEY, base_url=UNIAPI_BASE)

_SYSTEM_PROMPT = """\
你是一位专业的 AP 统计家教老师，正在帮学生分析一道做错的题目。

要求：
- 用中文回答，语气亲切但专业
- 2-4 句话，不要过长
- 第一句指出学生可能的错误原因（结合学生选项和正确答案）
- 第二句解释正确答案的核心逻辑
- 如有必要，第三句给出复习建议（具体到知识点）
- 不要重复题目原文，直接分析
"""


def _load_question(question_id: int) -> dict | None:
    if not os.path.exists(QUESTIONS_JSON):
        return None
    with open(QUESTIONS_JSON, encoding="utf-8") as f:
        questions = json.load(f)
    return next((q for q in questions if q["id"] == question_id), None)


def analyze_error(
    student_id: str,
    question_id: int,
    submitted_answer: str,
) -> str:
    """
    生成错题分析文字。
    网络失败或题目信息不足时返回通用提示。
    """
    q = _load_question(question_id)
    if not q:
        return f"正确答案是 {submitted_answer}，建议复习相关知识点。"

    correct = q.get("correct_answer", "").upper()
    question_text = q.get("question_text", "").strip()
    answer_text = q.get("answer_text", "").strip()
    topic_tags = q.get("topic_tags", [])
    options = q.get("options", {})

    # 学生薄弱点，供 prompt 参考
    profile = _load_student(student_id)
    weak_topics = profile.get("weak_topics", [])

    # 构造 prompt
    option_lines = "\n".join(f"  {k}: {v}" for k, v in options.items()) if options else ""
    user_prompt = f"""题目知识点：{', '.join(topic_tags)}
学生薄弱点：{', '.join(weak_topics) if weak_topics else '暂无记录'}

题目内容（OCR，仅供参考）：
{question_text or '（无文字信息）'}

选项：
{option_lines or '（无选项信息）'}

学生选了：{submitted_answer.upper()}
正确答案：{correct}

解析（OCR，仅供参考）：
{answer_text or '（无解析文字）'}

请分析学生的错误原因并给出建议。"""

    try:
        resp = _client.chat.completions.create(
            model="claude-sonnet-4-5",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=200,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        # 降级：返回基础提示
        tags_str = "、".join(topic_tags) if topic_tags else "相关知识点"
        return (
            f"你选了 {submitted_answer.upper()}，正确答案是 {correct}。"
            f"建议复习 {tags_str}，对照解析图片仔细理解。"
        )


# ── CLI 调试 ──────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python error_analyzer.py <student_id> <question_id> <submitted_answer>")
        sys.exit(1)

    sid = sys.argv[1]
    qid = int(sys.argv[2])
    ans = sys.argv[3]

    analysis = analyze_error(sid, qid, ans)
    print(analysis)
