#!/usr/bin/env python3
"""
全局配置：所有可调参数集中在此，其他模块从这里 import。
密钥从 .env 文件读取，不提交到 git。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 推送引擎参数（默认值，可被 feishu/app_config.json 覆盖）────
PUSH_COUNT                = 3      # 每次推送题目数量
DEDUP_DAYS                = 7      # 题目去重天数窗口（同一题不在此窗口内重复推送，0 表示不去重）
MASTERY_THRESHOLD         = 0.6    # 薄弱点判定阈值（知识点正确率低于此值视为薄弱）
DIFFICULTY_UP_THRESHOLD   = 0.8    # 近 10 题正确率 ≥ 此值则提升难度档位
DIFFICULTY_DOWN_THRESHOLD = 0.5    # 近 10 题正确率 < 此值则降低难度档位

# 从 feishu/app_config.json 加载运行时覆盖值
def _load_app_config() -> dict:
    import json as _json
    p = Path(__file__).parent / "feishu" / "app_config.json"
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}

_app_cfg = _load_app_config()
if "dedup_days"                in _app_cfg: DEDUP_DAYS                = int(_app_cfg["dedup_days"])
if "mastery_threshold"         in _app_cfg: MASTERY_THRESHOLD         = float(_app_cfg["mastery_threshold"])
if "difficulty_up_threshold"   in _app_cfg: DIFFICULTY_UP_THRESHOLD   = float(_app_cfg["difficulty_up_threshold"])
if "difficulty_down_threshold" in _app_cfg: DIFFICULTY_DOWN_THRESHOLD = float(_app_cfg["difficulty_down_threshold"])

# ── API 配置 ──────────────────────────────────────────────────
UNIAPI_KEY  = os.environ["UNIAPI_KEY"]
UNIAPI_BASE = os.getenv("UNIAPI_BASE", "https://hk.uniapi.io/v1")
API_MODEL   = "gpt-5.4"
DPI         = 200

# ── 文件路径 ──────────────────────────────────────────────────
# 基准目录：config.py 所在的项目根目录（Wenshu-main/），支持任意部署位置
BASE_DIR       = Path(__file__).parent.resolve()
OUTPUT_DIR     = str(BASE_DIR / "output")
QUESTIONS_JSON = str(BASE_DIR / "output" / "chenxi" / "questions.json")
STUDENTS_DIR   = str(BASE_DIR / "students")


def questions_json(teacher_id: str) -> Path:
    """返回指定老师的题库路径。

    用法：
        from config import questions_json
        p = questions_json("chenxi")   # -> BASE_DIR/output/chenxi/questions.json
    """
    return BASE_DIR / "output" / teacher_id / "questions.json"