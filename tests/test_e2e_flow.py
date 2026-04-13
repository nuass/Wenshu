"""
tests/test_e2e_flow.py

闭环集成测试：模拟飞书群内完整交互流程。

覆盖 feishu-question-push.ts 所调用的所有 Python 接口，
验证 JSON 输出格式与 TypeScript 侧约定一致。
测试通过即意味着 Python 层可直接在飞书场景中正常运行。

测试场景（单一 test_full_flow 顺序执行）：
  1.  老师推题：push_engine.push() → 验证 PushResult 格式
  2.  当日重复推题被拦截
  3.  老师账号调用 push 被拒绝
  4.  学生提交正确答案（intent_router 规则匹配 → record_answer）
  5.  学生提交错误答案 → 返回 show_answer_for
  6.  主动查看解析（show_answer 意图）
  7.  查看学习进度（show_progress 意图）
  8.  调整难度（adjust_difficulty 意图）
  9.  学生画像持久化验证

运行：
  cd auto_send
  python -m pytest tests/test_e2e_flow.py -v
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 测试常量 ──────────────────────────────────────────────────

_STUDENT_ID   = "e2e_test_student"
_TEACHER_ACCT = "e2e_test_teacher_account"
_QUESTIONS_JSON = str(Path(__file__).parent.parent / "output" / "chenxi" / "questions.json")


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def students_dir(tmp_path_factory):
    """临时学生目录，含最小化 roster.json。"""
    tmp = tmp_path_factory.mktemp("students")
    roster = {
        _STUDENT_ID: {
            "role": "student",
            "name": "E2E测试学生",
            "teacher_id": "chenxi",
            "subject": "AP统计",
        },
        _TEACHER_ACCT: {
            "role": "teacher",
            "name": "测试老师",
            "teacher_id": "chenxi",
            "subject": "AP统计",
        },
    }
    (tmp / "roster.json").write_text(
        json.dumps(roster, ensure_ascii=False), encoding="utf-8"
    )
    return tmp


@pytest.fixture(scope="module", autouse=True)
def patch_env(students_dir):
    """
    重定向所有模块的文件路径到测试环境，并 mock API 调用：
    - intent_router._client：抛异常 → 降级到规则匹配（不依赖真实 API）
    - error_analyzer._client：抛异常 → fallback 文字（不阻塞流程）
    - report_generator._client：返回固定文本（不依赖真实 API）
    """
    import push_engine
    import record_answer
    import answer_parser
    import report_generator
    import error_analyzer
    import intent_router
    import config

    mock_report_resp = MagicMock()
    mock_report_resp.choices = [MagicMock()]
    mock_report_resp.choices[0].message.content = "本周共练习了若干道题，继续加油！"

    patches = [
        # 路径重定向
        patch.object(push_engine,      "STUDENTS_DIR",  str(students_dir)),
        patch.object(push_engine,      "ROSTER_JSON",   students_dir / "roster.json"),
        patch.object(push_engine,      "QUESTIONS_JSON", _QUESTIONS_JSON),
        patch.object(record_answer,    "STUDENTS_DIR",  str(students_dir)),
        patch.object(record_answer,    "QUESTIONS_JSON", _QUESTIONS_JSON),
        patch.object(answer_parser,    "STUDENTS_DIR",  str(students_dir)),
        patch.object(report_generator, "STUDENTS_DIR",  str(students_dir)),
        patch.object(error_analyzer,   "STUDENTS_DIR",  str(students_dir)),
        patch.object(error_analyzer,   "QUESTIONS_JSON", _QUESTIONS_JSON),
        patch.object(config,           "STUDENTS_DIR",  str(students_dir)),
        # API mock
        patch.object(
            intent_router._client.chat.completions, "create",
            side_effect=RuntimeError("mock: no API"),
        ),
        patch.object(
            error_analyzer._client.chat.completions, "create",
            side_effect=RuntimeError("mock: no API"),
        ),
        patch.object(
            report_generator._client.chat.completions, "create",
            return_value=mock_report_resp,
        ),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


# ── 工具函数 ──────────────────────────────────────────────────

def _load_profile(students_dir: Path, student_id: str) -> dict:
    path = students_dir / f"{student_id}.json"
    assert path.exists(), f"学生画像不存在：{path}"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 闭环集成测试 ──────────────────────────────────────────────

def test_full_flow(students_dir):
    """
    按飞书实际交互顺序验证完整流程。
    每个 Step 对应 feishu-question-push.ts 的一个调用路径。
    """
    import push_engine
    import intent_router

    # ─────────────────────────────────────────────────────────
    # Step 1: 老师模式 — push_engine.push()
    #   TypeScript: handleQuestionPush() → runPushEngine(studentId)
    # ─────────────────────────────────────────────────────────
    result = push_engine.push(_STUDENT_ID)

    assert isinstance(result.get("questions"), list), f"未推出题目：{result}"
    assert len(result["questions"]) == 3, f"应推 3 道题，实际：{len(result['questions'])}"
    assert result["student_id"] == _STUDENT_ID
    assert result["teacher_id"] == "chenxi"

    # 验证每道题包含 TypeScript 侧需要的字段
    for q in result["questions"]:
        assert "question_id"    in q, f"缺少 question_id：{q}"
        assert "question_image" in q, f"缺少 question_image：{q}"
        assert "topic_tags"     in q, f"缺少 topic_tags：{q}"
        assert "difficulty"     in q, f"缺少 difficulty：{q}"
        img_path = Path(q["question_image"])
        assert img_path.exists(), f"题目图片不存在：{img_path}"

    pushed_ids_r1 = [q["question_id"] for q in result["questions"]]

    # ─────────────────────────────────────────────────────────
    # Step 2: 同日第二次推题 — 应返回 3 道全新题目（7 天去重）
    #   push_engine 的去重窗口是 7 天（不是每日上限），同日可多次推题，
    #   但已推过的题目不会重复出现。
    #   注意：第二次 push 会更新 context，后续答题基于 result2
    # ─────────────────────────────────────────────────────────
    result2 = push_engine.push(_STUDENT_ID)
    assert len(result2["questions"]) == 3, f"第二次推题应再得 3 道：{result2}"
    pushed_ids2 = {q["question_id"] for q in result2["questions"]}
    assert not pushed_ids2 & set(pushed_ids_r1), "第二次推题不应与第一次重复"

    # context 已更新到 result2，后续答题使用 result2 的题目和答案
    pushed_ids = [q["question_id"] for q in result2["questions"]]
    correct_ans_q0 = (result2["questions"][0].get("correct_answer") or "A").upper()
    correct_ans_q1 = (result2["questions"][1].get("correct_answer") or "A").upper()

    # ─────────────────────────────────────────────────────────
    # Step 3: 老师账号调用 push 被拒绝
    #   TypeScript: handleQuestionPush() — roster 拦截
    # ─────────────────────────────────────────────────────────
    result_teacher = push_engine.push(_TEACHER_ACCT)
    assert result_teacher["questions"] == [], "老师账号不应收到题目"
    assert "error" in result_teacher, "老师账号应返回 error 字段"

    # ─────────────────────────────────────────────────────────
    # Step 4: 学生提交正确答案
    #   TypeScript: handleStudentMessage() → runIntentRouter(studentId, "第1题A")
    #   intent_router 规则匹配（API 调用被 mock 为失败 → fallback）
    # ─────────────────────────────────────────────────────────
    msg_correct = f"第1题{correct_ans_q0}"
    route_ok = intent_router.route(_STUDENT_ID, msg_correct, chat_id="")

    assert route_ok.get("reply"), f"提交正确答案应有回复：{route_ok}"
    assert "✅" in route_ok["reply"], f"正确答案应有 ✅：{route_ok['reply']}"
    # 答对时不触发解析推送
    assert not route_ok.get("show_answer_for"), f"答对不应返回 show_answer_for：{route_ok}"

    # ─────────────────────────────────────────────────────────
    # Step 5: 学生提交错误答案 → 触发解析图推送
    #   TypeScript: show_answer_for != null → sendAnswerImage()
    # ─────────────────────────────────────────────────────────
    wrong_ans_q1 = "B" if correct_ans_q1 != "B" else "A"
    msg_wrong = f"第2题{wrong_ans_q1}"
    route_wrong = intent_router.route(_STUDENT_ID, msg_wrong, chat_id="")

    assert route_wrong.get("reply"), f"提交错误答案应有回复：{route_wrong}"
    assert "❌" in route_wrong["reply"], f"错误答案应有 ❌：{route_wrong['reply']}"
    # 答错应返回 show_answer_for 供 TypeScript 侧发解析图
    assert route_wrong.get("show_answer_for") == pushed_ids[1], (
        f"show_answer_for 应为第2题 ID={pushed_ids[1]}，实际：{route_wrong}"
    )

    # ─────────────────────────────────────────────────────────
    # Step 6: 主动查看解析
    #   TypeScript: handleStudentMessage() → show_answer_for != null
    # ─────────────────────────────────────────────────────────
    route_show = intent_router.route(_STUDENT_ID, "看解析", chat_id="")
    assert route_show.get("show_answer_for") is not None, (
        f"查看解析应返回 show_answer_for：{route_show}"
    )
    # 返回最近推送的最后一题 ID
    assert route_show["show_answer_for"] == pushed_ids[-1], (
        f"show_answer_for 应为最后一题 ID={pushed_ids[-1]}，实际：{route_show}"
    )

    # ─────────────────────────────────────────────────────────
    # Step 7: 查看学习进度
    #   TypeScript: handleStudentMessage() → reply 包含报告文字
    # ─────────────────────────────────────────────────────────
    route_progress = intent_router.route(_STUDENT_ID, "我的进度", chat_id="")
    assert route_progress.get("reply"), f"进度查询应有回复：{route_progress}"
    # report_generator API 被 mock，返回固定文本或降级原始数据，均可接受
    assert len(route_progress["reply"]) > 5, f"进度回复内容太短：{route_progress['reply']}"

    # ─────────────────────────────────────────────────────────
    # Step 8: 调整难度
    #   TypeScript: handleStudentMessage() → reply 包含"难度"
    # ─────────────────────────────────────────────────────────
    profile_before = _load_profile(students_dir, _STUDENT_ID)
    diff_before = profile_before.get("current_difficulty", 3)

    route_diff = intent_router.route(_STUDENT_ID, "简单点", chat_id="")
    assert route_diff.get("reply"), f"调整难度应有回复：{route_diff}"
    assert "难度" in route_diff["reply"], f"回复应包含'难度'：{route_diff['reply']}"

    profile_after = _load_profile(students_dir, _STUDENT_ID)
    assert profile_after["current_difficulty"] == max(1, diff_before - 1), (
        f"难度应降低 1 档：{diff_before} → {profile_after['current_difficulty']}"
    )

    # ─────────────────────────────────────────────────────────
    # Step 9: 学生画像持久化验证
    # ─────────────────────────────────────────────────────────
    profile = _load_profile(students_dir, _STUDENT_ID)

    assert profile["teacher_id"] == "chenxi", "teacher_id 应从 roster 同步"
    assert len(profile["send_history"]) >= 3, "应有 ≥3 条推送历史"

    # 验证 Step 4 正确答案和 Step 5 错误答案都已写入
    answered = [r for r in profile["send_history"] if r.get("answered")]
    assert len(answered) >= 2, f"应有 ≥2 条已回答记录，实际：{len(answered)}"

    correct_records  = [r for r in answered if r["is_correct"]]
    wrong_records    = [r for r in answered if r["is_correct"] is False]
    assert len(correct_records) >= 1, "应有至少 1 道正确记录"
    assert len(wrong_records)   >= 1, "应有至少 1 道错误记录"

    # 知识点掌握度应已更新（至少有一个知识点）
    assert profile.get("topic_mastery"), "答题后 topic_mastery 应已更新"
