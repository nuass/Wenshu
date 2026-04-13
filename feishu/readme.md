# 飞书机器人配置

## 机器人 A — 文殊之虾

| 项目 | 值 |
|------|----|
| App ID（凭证） | `cli_a939cc8377ba1bd7` |
| App Secret（密钥） | `TipFQwMWNJJyEEyPtJJZcgygEhQ5Rb6q` |
| 机器人名称 | 文殊之虾 |
| 用途 | APtest1 群，绑定 chenxi 老师学生 |

## 机器人 B — AgenticBenchmarkClaw

| 项目 | 值 |
|------|----|
| App ID（凭证） | `cli_a93a61e3297bdbde` |
| App Secret（密钥） | `5yKOgd1irDdJRcFnqVu4m6GfppdILdwu` |
| 机器人名称 | AgenticBenchmarkClaw |
| 用途 | APtest2 群备用 |

---

## 群信息

### APtest1

| 项目 | 值 |
|------|-----|
| chat_id | `oc_4003a1795f471dc00f6b5c90fc43da8d` |
| 机器人 | 文殊之虾（机器人 A） |

### APtest2

| 项目 | 值 |
|------|-----|
| chat_id | `oc_207139e6149fd6829e02367adc3cd0db` |
| 机器人 | AgenticBenchmarkClaw（机器人 B） |

---

## 用户 open_id

| 角色 | open_id |
|------|---------|
| 管理员（学生） | `ou_ef54461cdd57b75bd4b174e04032205d` |
| 老师（晨曦） | `ou_760f8a027f1ec04ad6edb55e394693b4` |

---

## OpenClaw 链路

飞书平台配置的事件回调 URL → OpenClaw 公网地址 → 本机 auto_send 服务。

运行代码位于：`/Users/cony.zhangbjgmail.com/dev/openclaw/extensions/feishu/src/question-push.ts`

本目录 `feishu-question-push.ts` 是 question-push.ts 的**参考副本**，改完后需手动同步到 openclaw。
