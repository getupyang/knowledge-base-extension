#!/bin/bash
# 工作台一键启动脚本
# 用法：bash start.sh

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
CONFIG_FILE="$HOME/.kb_config"

echo "=== 启动工作台 ==="

# ── 加载配置 ──────────────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
  echo "✗ 未找到配置文件 $CONFIG_FILE"
  echo "  请先运行：bash $REPO_DIR/setup.sh"
  exit 1
fi
source "$CONFIG_FILE"

export KB_DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
LOG_DIR="$KB_DATA_DIR/.logs"
mkdir -p "$LOG_DIR"
echo "→ 本地记忆目录：$KB_DATA_DIR"

export KB_NOTION_TOKEN="$NOTION_TOKEN"
export KB_NOTION_DATABASE_ID="$NOTION_DATABASE_ID"
export KB_CLAUDE_BIN="$CLAUDE_BIN"
export HOME="$HOME"  # 显式传递，防止 uvicorn 子进程丢失 HOME 导致 claude 找不到认证配置
# RESEARCH_DIR：server.py 的文档根目录，从 ~/.kb_config 读（指向用户的私人 MD 文件目录）
export RESEARCH_DIR="${RESEARCH_DIR:-$BACKEND_DIR}"

# ── 检查并停止已有进程 ────────────────────────────────────
for PORT in 8765 8766; do
  PID=$(lsof -ti :$PORT 2>/dev/null)
  if [ -n "$PID" ]; then
    echo "端口 $PORT 已被占用（PID $PID），先停掉..."
    kill $PID 2>/dev/null
    sleep 1
  fi
done

# ── 启动知识库服务器（8765）────────────────────────────────
echo "→ 启动知识库服务器 (8765)..."
cd "$BACKEND_DIR"
nohup python3 server.py > "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!

# ── 启动 Agent API（8766）─────────────────────────────────
echo "→ 启动 Agent API (8766)..."
nohup python3 -m uvicorn agent_api:app --host 127.0.0.1 --port 8766 > "$LOG_DIR/agent_api.log" 2>&1 &
AGENT_PID=$!

for i in {1..30}; do
  if curl -s http://localhost:8766/health > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

# ── 启动异步 worker（jobs 表后台任务）──────────────────────
WORKER_PID_FILE="$LOG_DIR/worker.pid"
if [ -f "$WORKER_PID_FILE" ]; then
  OLD_WORKER_PID="$(cat "$WORKER_PID_FILE" 2>/dev/null)"
  if [ -n "$OLD_WORKER_PID" ] && ps -p "$OLD_WORKER_PID" > /dev/null 2>&1; then
    echo "worker 已在运行（PID $OLD_WORKER_PID），先停掉..."
    kill "$OLD_WORKER_PID" 2>/dev/null
    sleep 1
  fi
fi

echo "→ 启动后台 worker..."
nohup python3 worker.py > "$LOG_DIR/worker.log" 2>&1 &
WORKER_PID=$!
echo "$WORKER_PID" > "$WORKER_PID_FILE"

sleep 4

# ── 验证 ──────────────────────────────────────────────────
ALL_OK=true
if curl -s http://localhost:8765 > /dev/null 2>&1; then
  echo "✓ 知识库服务器：http://localhost:8765"
else
  echo "✗ 知识库服务器启动失败，查看日志：$LOG_DIR/server.log"
  ALL_OK=false
fi

if curl -s http://localhost:8766/health > /dev/null 2>&1; then
  echo "✓ Agent API：http://localhost:8766"
else
  echo "✗ Agent API 启动失败，查看日志：$LOG_DIR/agent_api.log"
  ALL_OK=false
fi

if ps -p "$WORKER_PID" > /dev/null 2>&1; then
  echo "✓ Worker：PID $WORKER_PID"
else
  echo "✗ Worker 启动失败，查看日志：$LOG_DIR/worker.log"
  ALL_OK=false
fi

if $ALL_OK; then
  echo ""
  echo "工作台已就绪。"
  echo "  知识库：http://localhost:8765"
  echo "  记忆笔记本：http://localhost:8765/notebook/"
  echo "  停止服务：kill $SERVER_PID $AGENT_PID $WORKER_PID"
fi
