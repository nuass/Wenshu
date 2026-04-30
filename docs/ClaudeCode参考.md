# Claude Code 功能参考指南

本文档整理了 [Claude Code](https://github.com/anthropics/claude-code) 源码中可供 Wenshu-main 项目参考借鉴的功能点。

---

## 📋 目录

- [记忆系统设计参考](#1️⃣-记忆系统设计参考)
- [任务管理系统](#2️⃣-任务管理系统)
- [工作流模式](#3️⃣-工作流模式-plan-mode)
- [技能系统](#4️⃣-技能系统-skills)
- [会话压缩](#5️⃣-会话压缩-compact)
- [快捷命令系统](#6️⃣-快捷命令系统-slash-commands)
- [反馈调查系统](#7️⃣-反馈调查系统)
- [通知系统](#8️⃣-通知系统-notifications)
- [多 Agent 协调](#9️⃣-多-agent-协调)
- [会话历史与恢复](#🔟-会话历史与恢复)
- [权限与安全](#1️⃣1️⃣-权限与安全)
- [数据迁移系统](#1️⃣2️⃣-数据迁移系统)
- [优先实现清单](#🎯-优先推荐实现清单)

---

## 1️⃣ 记忆系统设计参考

### CC-main 相关文件
- `memory_store.py` (Wenshu-main 已实现)
- `src/tools/BriefTool/` (内容摘要)
- `src/services/compact/` (会话压缩)

### 可借鉴功能

#### ✅ 已实现但可增强
- **Frontmatter 解析** - 已实现，可扩展支持更多元数据字段
- **全局索引更新** - 已实现，可优化为增量更新
- **记忆摘要注入** - 已实现，可提升摘要质量

#### 🆕 建议新增
```python
# memory_store.py 可添加：

def consolidate_memories(student_id: str, api_key: str = "") -> str:
    """
    调用 LLM 自动整合教学反馈记忆
    - 合并重复洞察
    - 删除过时信息
    - 保留高价值内容
    """

def search_memories(student_id: str, keyword: str) -> list[dict]:
    """
    按关键词检索学生记忆
    - 支持全文搜索
    - 支持知识点标签
    - 支持时间范围过滤
    """

def score_memory_importance(student_id: str, memory_type: str) -> float:
    """
    评分记忆重要性
    - 近期记忆权重高
    - 错题相关权重高
    - 知识点掌握状态
    """

def cleanup_old_memories(student_id: str, days: int = 90) -> None:
    """
    清理过期记忆
    - 自动压缩归档
    - 保留重要记忆
    """
```

---

## 2️⃣ 任务管理系统

### CC-main 相关文件
- `src/tools/TaskCreateTool/`
- `src/tools/TaskUpdateTool/`
- `src/tools/TaskListTool/`
- `src/tools/TaskStopTool/`
- `src/tools/TaskOutputTool/`
- `src/components/tasks/`

### 可借鉴功能

#### 建议实现结构
```
Wenshu-main/
├── task_system.py          # 任务管理核心
├── commands/tasks.py       # 任务相关命令
└── tests/test_tasks.py     # 测试
```

#### 任务数据模型
```python
# task_system.py

@dataclass
class LearningTask:
    task_id: str
    student_id: str
    title: str
    description: str
    task_type: str           # "exercise" | "review" | "exam_prep"
    topic: Optional[str]
    difficulty: int
    question_ids: list[int]
    status: str              # "pending" | "in_progress" | "completed" | "abandoned"
    progress: float          # 0.0 - 1.0
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    deadline: Optional[datetime]
```

#### 任务命令设计
```
/任务 列表
/任务 创建 [标题] [知识点] [难度] [数量]
/任务 开始 <task_id>
/任务 完成 <task_id>
/任务 放弃 <task_id>
/任务 进度 <task_id>
/任务 提醒 <task_id> [时间]
```

---

## 3️⃣ 工作流模式（Plan Mode）

### CC-main 相关文件
- `src/tools/EnterPlanModeTool/`
- `src/tools/ExitPlanModeTool/`
- `src/tools/VerifyPlanExecutionTool/`

### 可借鉴功能

#### 学习计划模式
```
/plan
  └── 进入计划模式 → 引导学生制定学习计划

1. 选择目标（本周/本月/考前）
2. 选择知识点范围
3. 设定难度和题量
4. 安排时间节点
5. 确认并执行计划

/execute_plan
  └── 执行计划，自动按步骤推送

/verify_plan
  └── 验证计划完成情况
```

---

## 4️⃣ 技能系统（Skills）

### CC-main 相关文件
- `src/skills/bundled/claude-api/`
- `src/skills/bundled/verify/`
- `src/tools/SkillTool/`

### 可借鉴功能

#### 教学技能库设计
```
skills/
├── __init__.py
├── base.py                  # 基础技能类
├── explanation.py           # 知识点讲解技能
├── error_analysis.py        # 错题分析技能
├── learning_method.py       # 学习方法指导技能
├── encouragement.py         # 激励鼓励技能
└── exam_prep.py             # 考试准备技能
```

#### 技能触发机制
```python
# 自动根据学生情况触发对应技能
- 连续错题 → 触发知识点讲解
- 学习停滞 → 触发学习方法指导
- 进步明显 → 触发激励鼓励
- 考试临近 → 触发考试准备
```

---

## 5️⃣ 会话压缩（Compact）

### CC-main 相关文件
- `src/services/compact/`

### 可借鉴功能

#### 对话历史自动压缩
```python
# conversation_compactor.py

def compact_conversation(
    student_id: str,
    keep_days: int = 7,
    summary_old: bool = True
) -> list[dict]:
    """
    压缩对话历史
    - 保留最近 N 天的原始对话
    - 更早的对话自动摘要
    - 标记重要信息不压缩
    """

def extract_important_messages(messages: list[dict]) -> list[dict]:
    """
    提取重要消息
    - 错题相关
    - 知识点讲解
    - 学生反馈
    - 计划制定
    """
```

---

## 6️⃣ 快捷命令系统（Slash Commands）

### CC-main 相关文件
- `src/commands/` (100+ 命令)
- `src/commands.ts`

### 可借鉴功能

#### 建议添加的命令

| 命令 | 功能 |
|------|------|
| `/出题 [知识点] [难度] [数量]` | 推送题目 |
| `/复习 [错题/知识点/全部]` | 复习模式 |
| `/进度 [本周/本月/全部]` | 查看学习进度 |
| `/记忆 [查看/添加/删除]` | 管理学习记忆 |
| `/难度 [1-5]` | 调整难度 |
| `/任务 [列表/创建/完成]` | 任务管理 |
| `/plan` | 进入计划模式 |
| `/统计` | 查看统计数据 |
| `/帮助` | 显示帮助 |

#### 命令系统实现
```python
# commands.py

COMMAND_REGISTRY = {
    "出题": handle_push,
    "复习": handle_review,
    "进度": handle_progress,
    "记忆": handle_memory,
    "难度": handle_difficulty,
    "任务": handle_tasks,
    "plan": handle_plan,
    "统计": handle_stats,
    "帮助": handle_help,
}

def parse_command(message: str) -> Optional[Tuple[str, list]]:
    """解析斜杠命令"""
    if message.startswith("/"):
        parts = message[1:].split(maxsplit=1)
        cmd = parts[0]
        args = parts[1].split() if len(parts) > 1 else []
        if cmd in COMMAND_REGISTRY:
            return cmd, args
    return None
```

---

## 7️⃣ 反馈调查系统

### CC-main 相关文件
- `src/components/FeedbackSurvey/`
- `src/hooks/notifs/`

### 可借鉴功能

#### 学习反馈收集
```python
# feedback_system.py

@dataclass
class FeedbackSurvey:
    survey_id: str
    student_id: str
    survey_type: str        # "experience" | "understanding" | "difficulty"
    questions: list[dict]
    answers: Optional[list[dict]]
    created_at: datetime
    completed_at: Optional[datetime]

def trigger_survey(
    student_id: str,
    survey_type: str,
    condition: dict
) -> Optional[FeedbackSurvey]:
    """
    根据条件触发调查
    - 连续错题 → 理解度调查
    - 学习一段时间 → 体验调查
    - 难度调整后 → 难度合适度调查
    """

def detect_frustration(messages: list[dict]) -> bool:
    """
    检测学生困难情绪
    - 负面词汇分析
    - 连续放弃行为
    - 长时间无进展
    """
```

---

## 8️⃣ 通知系统（Notifications）

### CC-main 相关文件
- `src/hooks/notifs/` (20+ 通知 Hooks)

### 可借鉴功能

#### 学习提醒系统
```python
# notifications.py

NOTIFICATION_TYPES = {
    "daily_push": "每日推题提醒",
    "review": "错题复习提醒",
    "milestone": "学习进度里程碑",
    "exam_countdown": "考试倒计时",
    "encouragement": "鼓励消息",
    "task_reminder": "任务截止提醒",
}

def schedule_notification(
    student_id: str,
    notification_type: str,
    send_time: datetime,
    data: dict
) -> str:
    """计划通知"""

def send_feishu_notification(
    student_id: str,
    message: str,
    card: Optional[dict] = None
) -> bool:
    """发送飞书通知"""
```

#### 通知触发场景
- 🕐 **每日固定时间** - 推题提醒
- 📚 **错题积累到 N 道** - 复习提醒
- 🎯 **连续答对 N 题** - 鼓励消息
- 📅 **考试临近** - 倒计时提醒
- ⏰ **任务截止前** - 任务提醒

---

## 9️⃣ 多 Agent 协调

### CC-main 相关文件
- `src/coordinator/`
- `src/buddy/`
- `src/tools/AgentTool/`
- `src/tools/TeamCreateTool/`

### 可借鉴功能

#### 多角色 AI 协作
```
agents/
├── __init__.py
├── base.py                  # Agent 基类
├── teacher_agent.py         # 教学专家（出题、讲解）
├── motivator_agent.py       # 心理激励（鼓励、关怀）
├── planner_agent.py         # 学习规划（制定计划）
├── analyst_agent.py         # 错题分析（分析错误原因）
└── coordinator.py           # Agent 协调器
```

#### Agent 协调流程
```
1. 学生输入 → 意图识别
2. 协调器选择合适的 Agent(s)
3. Agent 生成响应
4. 协调器整合响应
5. 发送给学生
```

---

## 🔟 会话历史与恢复

### CC-main 相关文件
- `src/assistant/sessionHistory.ts`
- `src/commands/resume/`
- `src/commands/rename/`
- `src/commands/share/`

### 可借鉴功能

#### 会话管理
```python
# session_manager.py

@dataclass
class Session:
    session_id: str
    student_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    is_archived: bool

def list_sessions(student_id: str, limit: int = 20) -> list[Session]:
    """列出学生的会话历史"""

def rename_session(session_id: str, new_title: str) -> None:
    """重命名会话"""

def export_session(session_id: str, format: str = "markdown") -> str:
    """导出会话记录"""

def share_session(session_id: str, teacher_id: str) -> str:
    """分享会话给老师"""
```

---

## 1️⃣1️⃣ 权限与安全

### CC-main 相关文件
- `src/types/permissions.ts`
- `src/components/BypassPermissionsModeDialog.tsx`
- `src/commands/permissions/`

### 可借鉴功能

#### 权限管理
```python
# permissions.py

PERMISSION_TYPES = {
    "view_student_data": "查看学生数据",
    "export_data": "导出数据",
    "modify_settings": "修改设置",
    "send_notifications": "发送通知",
}

def request_permission(
    teacher_id: str,
    student_id: str,
    permission: str,
    reason: str
) -> str:
    """请求权限授权"""

def check_permission(
    teacher_id: str,
    student_id: str,
    permission: str
) -> bool:
    """检查权限"""
```

---

## 1️⃣2️⃣ 数据迁移系统

### CC-main 相关文件
- `src/migrations/` (10+ 迁移脚本)

### 可借鉴功能

#### 迁移脚本目录
```
migrations/
├── __init__.py
├── 001_initial.py
├── 002_add_topic_mastery.py
├── 003_migrate_memory_format.py
├── 004_add_task_system.py
└── runner.py              # 迁移执行器
```

#### 迁移执行器
```python
# migrations/runner.py

def run_migrations(target_version: Optional[int] = None) -> None:
    """执行数据迁移"""
    current = get_current_version()
    migrations = get_pending_migrations(current, target_version)
    for migration in migrations:
        try:
            migration.up()
            update_version(migration.version)
        except Exception as e:
            migration.down()
            raise
```

---

## 🎯 优先推荐实现清单

| 优先级 | 功能 | 预期收益 | 预估工作量 |
|-------|------|---------|-----------|
| 🔴 高 | 斜杠命令系统 | 提升交互效率 | 1-2 天 |
| 🔴 高 | 任务管理系统 | 增强学习结构化 | 3-5 天 |
| 🟡 中 | 记忆自动整合 | 优化上下文质量 | 2-3 天 |
| 🟡 中 | 通知提醒系统 | 提高学习频率 | 2-3 天 |
| 🟡 中 | 会话历史管理 | 提升用户体验 | 2-3 天 |
| 🟢 低 | 多 Agent 协作 | 丰富交互体验 | 5-7 天 |
| 🟢 低 | 工作流模式 | 增强学习规划 | 3-5 天 |
| 🟢 低 | 反馈调查系统 | 收集学习反馈 | 2-3 天 |

---

## 📚 相关资源

- [Claude Code GitHub](https://github.com/anthropics/claude-code)
- [Wenshu-main 记忆系统说明](./记忆系统说明.md)
- [AGENTS.md (Claude Code)](../../CC-main/AGENTS.md)

---

*最后更新: 2026-04-30*
