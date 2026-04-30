# 飞书推题系统

AP 题目个性化推送与答题评判系统，基于飞书机器人 + OpenClaw Gateway。

## 架构

```
OpenClaw Gateway (cron + 消息收发)
        ↓
feishu_bot.py --mode teacher/student
        ↓
push_engine / intent_router / record_answer
        ↓
飞书群（推题图片 / 答题评判）
```

## 目录结构

```
Wenshu-main/
├── feishu_bot.py        # 入口：teacher 模式推题，student 模式意图路由
├── push_engine.py       # 选题逻辑（难度自适应、薄弱点、去重）
├── intent_router.py     # 消息意图识别与分发
├── answer_parser.py     # 从学生消息中提取答案
├── record_answer.py     # 评判对错，更新学生画像
├── error_analyzer.py    # 错题 LLM 分析
├── report_generator.py  # 周报生成
├── process_pdf.py       # PDF 解析入库
├── lark_cli_send.py     # 飞书消息发送（基于 lark-cli）
├── admin_server.py      # 管理后台 Flask API（端口 10187）
├── student_store.py     # 学生画像读写
├── config.py            # 全局配置
├── logger.py            # 结构化日志（JSONL）
├── feishu/
│   ├── cron_jobs.json   # openclaw cron 参考配置
│   ├── app_config.json  # 运行时可调参数（dedup_days 等）
│   ├── users.json       # 管理后台账号
│   ├── question-push.ts # openclaw 薄壳（调用 feishu_bot.py）
│   ├── bot.ts           # openclaw bot 入口
│   └── readme.md        # 飞书应用凭证说明
├── output/
│   ├── <teacher_id>/questions.json   # 题库
│   └── <teacher_id>/images/          # 题目/解析裁剪图
├── students/
│   ├── roster.json      # 学生-老师-群绑定关系
│   └── <open_id>.json   # 学生画像
├── logs/                # push/answer/grading 事件日志（JSONL）
├── copurs/              # 原始 PDF 题目文件
├── templates/admin.html # 管理后台前端
└── tests/               # 单元测试 + e2e 测试
```

## 快速开始

**环境变量**（加入 `~/.zshrc` 或 `~/.bashrc`）：
```bash
export AUTO_SEND_DIR="/path/to/Wenshu-main"
export PYTHON3_BIN="/usr/bin/python3"
```

**`.env` 文件**（项目根目录）：
```env
UNIAPI_KEY=your_api_key
UNIAPI_BASE=https://hk.uniapi.io/v1
```

**依赖安装**：
```bash
pip install flask requests python-dotenv werkzeug
```

**启动管理后台**：
```bash
python admin_server.py
# http://localhost:10187  默认账号 admin/admin123
```

**同步 cron 配置到 openclaw**：
```bash
cp feishu/cron_jobs.json ~/.openclaw/cron/jobs.json
```

## 常用命令

```bash
# 手动推题（测试）
python feishu_bot.py --mode teacher \
  --target-id ou_ef54461cdd57b75bd4b174e04032205d \
  --chat-id oc_4003a1795f471dc00f6b5c90fc43da8d

# 查看学生画像
python logger.py --student ou_ef54461cdd57b75bd4b174e04032205d

# 重启 openclaw gateway
openclaw gateway stop && openclaw gateway start
```

## 关键配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEDUP_DAYS` | 7 | 同一题去重天数窗口 |
| `PUSH_COUNT` | 3 | 每次推题数量（可按老师配置） |
| `mastery_threshold` | 0.6 | 知识点薄弱判定阈值 |
| `difficulty_up_threshold` | 0.8 | 近10题正确率≥此值则升难度 |
| `difficulty_down_threshold` | 0.5 | 近10题正确率<此值则降难度 |

运行时参数通过管理后台「系统设置」页修改，存入 `feishu/app_config.json`。

## 文档

| 文档 | 说明 |
|------|------|
| [记忆系统说明](./docs/记忆系统说明.md) | 记忆系统使用指南 |
| [ClaudeCode参考](./docs/ClaudeCode参考.md) | Claude Code 功能参考清单 |

## 开发路线图

参考 [ClaudeCode参考](./docs/ClaudeCode参考.md) 了解可借鉴的功能点。
