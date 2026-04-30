#!/usr/bin/env bash
# openclaw.sh — OpenClaw 启动 / 停止 / 重启 管理脚本
# 用法:
#   ./openclaw.sh start    启动 gateway
#   ./openclaw.sh stop     停止 gateway
#   ./openclaw.sh restart  重启 gateway
#   ./openclaw.sh status   查看运行状态
#   ./openclaw.sh log      实时查看日志

set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────────────────
OPENCLAW_DIR="/project/openclaw-0415-extracted/openclaw-0415"
BIND="loopback"
PORT="18789"
LOG_FILE="/tmp/openclaw-gateway.log"
PID_FILE="/tmp/openclaw-gateway.pid"
# ──────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[openclaw]${NC} $*"; }
warn() { echo -e "${YELLOW}[openclaw]${NC} $*"; }
err()  { echo -e "${RED}[openclaw]${NC} $*" >&2; }

# 查找所有相关进程 PID（gateway 主进程 + pnpm 包装进程）
find_pids() {
  pgrep -f "openclaw-gateway" 2>/dev/null || true
  pgrep -f "openclaw gateway" 2>/dev/null || true
}

is_running() {
  [[ -n "$(find_pids)" ]]
}

do_status() {
  if is_running; then
    PIDS=$(find_pids | sort -u | tr '\n' ' ')
    log "OpenClaw 正在运行 (PID: ${PIDS})"
    # 简单健康检查
    if curl -sf "http://127.0.0.1:${PORT}/healthz" -o /dev/null 2>/dev/null; then
      log "健康检查 ✓  http://127.0.0.1:${PORT}/healthz"
    else
      warn "健康检查 ✗  gateway 进程存在但 /healthz 未响应（可能仍在启动中）"
    fi
  else
    warn "OpenClaw 未在运行"
  fi
}

do_stop() {
  if ! is_running; then
    warn "OpenClaw 未在运行，无需停止"
    return 0
  fi

  log "正在停止 OpenClaw..."
  PIDS=$(find_pids | sort -u)
  for pid in $PIDS; do
    kill "$pid" 2>/dev/null && log "  已发送 SIGTERM → PID $pid" || true
  done

  # 等待最多 10 秒优雅退出
  for i in $(seq 1 10); do
    sleep 1
    if ! is_running; then
      log "OpenClaw 已停止 ✓"
      rm -f "$PID_FILE"
      return 0
    fi
  done

  # 强制 kill
  warn "进程未响应，强制终止..."
  PIDS=$(find_pids | sort -u)
  for pid in $PIDS; do
    kill -9 "$pid" 2>/dev/null || true
  done
  sleep 1
  rm -f "$PID_FILE"
  log "OpenClaw 已强制停止 ✓"
}

do_start() {
  if is_running; then
    warn "OpenClaw 已在运行，跳过启动（使用 restart 强制重启）"
    do_status
    return 0
  fi

  if [[ ! -d "$OPENCLAW_DIR" ]]; then
    err "目录不存在: $OPENCLAW_DIR"
    exit 1
  fi

  log "启动 OpenClaw gateway (bind=${BIND}, port=${PORT})..."
  pushd "$OPENCLAW_DIR" > /dev/null

  nohup pnpm openclaw gateway run \
    --bind "$BIND" \
    --port "$PORT" \
    --force \
    > "$LOG_FILE" 2>&1 &

  BGPID=$!
  popd > /dev/null
  echo "$BGPID" > "$PID_FILE"

  # 等待健康检查通过（最多 30 秒）
  log "等待服务就绪..."
  for i in $(seq 1 30); do
    sleep 1
    if curl -sf "http://127.0.0.1:${PORT}/healthz" -o /dev/null 2>/dev/null; then
      log "OpenClaw 启动成功 ✓  (PID: $BGPID)"
      log "日志文件: $LOG_FILE"
      return 0
    fi
    # 检查进程是否意外退出
    if ! kill -0 "$BGPID" 2>/dev/null; then
      err "进程意外退出，请查看日志: $LOG_FILE"
      tail -20 "$LOG_FILE" 2>/dev/null || true
      exit 1
    fi
  done

  warn "超时：进程仍在运行但健康检查未通过，请查看日志"
  log "  tail -f $LOG_FILE"
}

do_restart() {
  log "重启 OpenClaw..."
  do_stop
  sleep 1
  do_start
}

do_log() {
  if [[ ! -f "$LOG_FILE" ]]; then
    warn "日志文件不存在: $LOG_FILE（尚未启动过？）"
    exit 1
  fi
  log "实时日志 (Ctrl+C 退出): $LOG_FILE"
  tail -f "$LOG_FILE"
}

# ── 入口 ──────────────────────────────────────────────────────────────
CMD="${1:-}"
case "$CMD" in
  start)   do_start   ;;
  stop)    do_stop    ;;
  restart) do_restart ;;
  status)  do_status  ;;
  log)     do_log     ;;
  *)
    echo "用法: $0 {start|stop|restart|status|log}"
    echo ""
    echo "  start    启动 OpenClaw gateway"
    echo "  stop     停止 OpenClaw gateway"
    echo "  restart  重启 OpenClaw gateway"
    echo "  status   查看运行状态及健康检查"
    echo "  log      实时查看运行日志"
    exit 1
    ;;
esac
