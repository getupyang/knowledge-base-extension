#!/bin/bash
# 工作台一键启动脚本
# 用法：bash start.sh

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
CONFIG_FILE="$HOME/.kb_config"
LOG_DIR="$BACKEND_DIR/.logs"
mkdir -p "$LOG_DIR"

echo "=== 启动工作台 ==="

# ── 加载配置 ──────────────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
  echo "✗ 未找到配置文件 $CONFIG_FILE"
  echo "  请先运行：bash $REPO_DIR/setup.sh"
  exit 1
fi
source "$CONFIG_FILE"

export KB_NOTION_TOKEN="$NOTION_TOKEN"
export KB_NOTION_DATABASE_ID="$NOTION_DATABASE_ID"
export KB_CLAUDE_BIN="$CLAUDE_BIN"
export KB_RESEARCH_DIR="$BACKEND_DIR"
export RESEARCH_DIR="$BACKEND_DIR"

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

sleep 2

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

if $ALL_OK; then
  echo ""
  echo "工作台已就绪。"
  echo "  知识库：http://localhost:8765"
  echo "  停止服务：kill $SERVER_PID $AGENT_PID"
fi
