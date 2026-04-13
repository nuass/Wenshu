/**
 * question-push.ts  (thin shim)
 *
 * 识别飞书消息中的推题指令并将消息处理委托给 auto_send/feishu_bot.py。
 * 所有业务逻辑（选题、意图识别、图片上传、飞书发送）均在 Python 侧完成。
 *
 * 仅保留：
 *   - parseQuestionPushTarget()  纯字符串解析，无 API 依赖
 *   - handleQuestionPush()       老师模式入口，调 feishu_bot.py --mode teacher
 *   - handleStudentMessage()     学生模式入口，调 feishu_bot.py --mode student
 */

import { execFile } from "node:child_process";
import * as os from "node:os";
import * as path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

// ── 配置 ──────────────────────────────────────────────────────

const AUTO_SEND_DIR = process.env.AUTO_SEND_DIR ?? path.join(os.homedir(), "dev", "auto_send");

// macOS app 启动的 Node 进程 PATH 精简，需要用绝对路径找到 python3
const PYTHON3_BIN = process.env.PYTHON3_BIN ?? "/opt/anaconda3/bin/python3";

const BOT_SCRIPT = path.join(AUTO_SEND_DIR, "feishu_bot.py");

// ── 工具函数 ──────────────────────────────────────────────────

async function runFeishuBot(args: string[]): Promise<void> {
  try {
    const { stdout, stderr } = await execFileAsync(PYTHON3_BIN, [BOT_SCRIPT, ...args], {
      cwd: AUTO_SEND_DIR,
      timeout: 60_000,
    });
    if (stderr) console.log(`[question-push] feishu_bot stderr: ${stderr}`);
    if (stdout) console.log(`[question-push] feishu_bot result: ${stdout.trim()}`);
  } catch (err) {
    console.error(`[question-push] feishu_bot failed: ${String(err)}`);
    throw err;
  }
}

// ── 指令识别 ──────────────────────────────────────────────────

/**
 * 从消息文本中提取目标用户的 open_id（老师模式：给 @学生 出题）。
 *
 * 过滤规则：
 *   - @的是发消息者自己（引用回复场景）→ null
 *   - 消息主体像答案提交（答案/选A 等）→ null
 */
export function parseQuestionPushTarget(content: string, senderOpenId?: string): string | null {
  const match = content.match(/<at\s+user_id="([^"]+)"[^>]*>/);
  if (!match) return null;
  const targetId = match[1];

  // 自我 @mention 过滤
  if (senderOpenId && targetId === senderOpenId) return null;

  // 答案内容过滤
  const plainText = content
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (/答案|[选答]\s*[ABCD]|^[ABCD][ABCD\s、，,]*$/i.test(plainText)) return null;

  return targetId;
}

// ── 老师模式 ──────────────────────────────────────────────────

export async function handleQuestionPush(params: {
  cfg?: unknown;
  targetOpenId: string;
  replyToChatId: string;
  replyToMessageId?: string;
  accountId?: string;
  log?: (...args: unknown[]) => void;
}): Promise<boolean> {
  const { targetOpenId, replyToChatId, log = console.log } = params;
  log(`[question-push] teacher mode → feishu_bot.py, target=${targetOpenId}`);

  await runFeishuBot([
    "--mode",
    "teacher",
    "--target-id",
    targetOpenId,
    "--chat-id",
    replyToChatId,
  ]);

  return true;
}

// ── 学生模式 ──────────────────────────────────────────────────

export async function handleStudentMessage(params: {
  cfg?: unknown;
  senderOpenId: string;
  message: string;
  chatId: string;
  replyToMessageId?: string;
  accountId?: string;
  log?: (...args: unknown[]) => void;
}): Promise<boolean> {
  const { senderOpenId, message, chatId, log = console.log } = params;
  log(`[question-push] student mode → feishu_bot.py, sender=${senderOpenId}`);

  await runFeishuBot([
    "--mode",
    "student",
    "--sender-id",
    senderOpenId,
    "--chat-id",
    chatId,
    "--message",
    message,
  ]);

  return true;
}
