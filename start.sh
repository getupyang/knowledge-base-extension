#!/bin/bash
# 工作台一键启动脚本
# 用法：bash start.sh

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
CONFIG_FILE="$HOME/.kb_config"

if [ "${1:-}" = "--install-login-item" ] || [ "${1:-}" = "--enable-auto-start" ]; then
  "$REPO_DIR/scripts/install-launch-agent"
  exit $?
fi

echo "=== 启动工作台 ==="

# ── 加载配置 ──────────────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
  echo "✗ 未找到配置文件 $CONFIG_FILE"
  echo "  还没有完成首次安装和 AI 服务配置。"
  echo "  请先运行：bash $REPO_DIR/setup.sh"
  exit 1
fi
set -a
source "$CONFIG_FILE"
set +a

export KB_DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
LOG_DIR="$KB_DATA_DIR/.logs"
mkdir -p "$LOG_DIR"
echo "→ 本地记忆目录：$KB_DATA_DIR"

export KB_NOTION_TOKEN="$NOTION_TOKEN"
export KB_NOTION_DATABASE_ID="$NOTION_DATABASE_ID"
export KB_CLAUDE_BIN="${MEMAI_CLAUDE_BIN:-${CLAUDE_BIN:-}}"
export MEMAI_LLM_PROVIDER="${MEMAI_LLM_PROVIDER:-auto}"
export MEMAI_LOCAL_AGENT="${MEMAI_LOCAL_AGENT:-none}"
export MEMAI_LLM_FALLBACK="${MEMAI_LLM_FALLBACK:-fail}"
export HOME="$HOME"  # 显式传递，防止 uvicorn 子进程丢失 HOME 导致 claude 找不到认证配置
export NO_PROXY="localhost,127.0.0.1,::1,${NO_PROXY:-}"
export no_proxy="localhost,127.0.0.1,::1,${no_proxy:-}"
# RESEARCH_DIR：server.py 的文档根目录，从 ~/.kb_config 读（指向用户的私人 MD 文件目录）
export RESEARCH_DIR="${RESEARCH_DIR:-$BACKEND_DIR}"

local_curl() {
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY curl --noproxy "*" "$@"
}

print_llm_runtime_summary() {
  provider="${MEMAI_LLM_PROVIDER:-auto}"
  local_agent="${MEMAI_LOCAL_AGENT:-none}"
  fallback="${MEMAI_LLM_FALLBACK:-fail}"

  echo "→ 当前 AI 服务："
  case "$provider" in
    api)
      api_provider="${MEMAI_LLM_API_PROVIDER:-custom}"
      case "$api_provider" in
        qwen)
          provider_label="千问 / Qwen API"
          ;;
        openrouter)
          provider_label="OpenRouter API"
          ;;
        openai)
          provider_label="OpenAI API"
          ;;
        deepseek)
          provider_label="DeepSeek API"
          ;;
        kimi|moonshot)
          provider_label="Kimi / Moonshot API"
          ;;
        *)
          provider_label="OpenAI-compatible API"
          ;;
      esac
      echo "  服务：$provider_label"
      echo "  模型：${MEMAI_LLM_MODEL:-未设置，后端会按服务商默认值处理}"
      echo "  Base URL：${MEMAI_LLM_BASE_URL:-未设置，后端会按服务商默认值处理}"
      ;;
    codex_cli)
      echo "  服务：Codex 直连"
      echo "  模型：${MEMAI_CODEX_MODEL:-Codex CLI 默认模型}"
      if [ -n "${MEMAI_CODEX_BIN:-}" ]; then
        echo "  路径：$MEMAI_CODEX_BIN"
      fi
      ;;
    claude_code)
      echo "  服务：Claude Code 直连"
      echo "  模型：${MEMAI_CLAUDE_MODEL:-Claude Code 默认模型}"
      claude_base_url="${MEMAI_CLAUDE_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"
      if [ -n "$claude_base_url" ]; then
        echo "  Claude Base URL：已设置"
      fi
      if [ -n "${MEMAI_CLAUDE_BIN:-${CLAUDE_BIN:-}}" ]; then
        echo "  路径：${MEMAI_CLAUDE_BIN:-${CLAUDE_BIN:-}}"
      fi
      ;;
    auto)
      if [ "$local_agent" = "codex_cli" ]; then
        echo "  服务：自动模式（优先 Codex 直连）"
        echo "  模型：${MEMAI_CODEX_MODEL:-Codex CLI 默认模型}"
      elif [ "$local_agent" = "claude_code" ]; then
        echo "  服务：自动模式（优先 Claude Code 直连）"
        echo "  模型：${MEMAI_CLAUDE_MODEL:-Claude Code 默认模型}"
        claude_base_url="${MEMAI_CLAUDE_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"
        if [ -n "$claude_base_url" ]; then
          echo "  Claude Base URL：已设置"
        fi
      elif [ -n "${MEMAI_LLM_API_KEY:-}" ] || [ -n "${OPENROUTER_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ] || [ -n "${DASHSCOPE_API_KEY:-}" ] || [ -n "${QWEN_API_KEY:-}" ] || [ -n "${BAILIAN_API_KEY:-}" ]; then
        echo "  服务：自动模式（使用已配置的 API 服务）"
        echo "  API Provider：${MEMAI_LLM_API_PROVIDER:-custom}"
        echo "  API 模型：${MEMAI_LLM_MODEL:-未设置，后端会按服务商默认值处理}"
      else
        echo "  服务：自动选择可用服务"
        echo "  提示：如果只安装了 Claude Code 或 Codex，后端会自动使用它。"
        echo "  如果启动后仍不可用，请运行：sh choose_ai_service.sh"
      fi
      ;;
    *)
      echo "  服务：$provider"
      echo "  模型：${MEMAI_LLM_MODEL:-未设置}"
      ;;
  esac

  if [ "$fallback" != "fail" ]; then
    echo "  失败回退：$fallback"
  fi
}

print_llm_runtime_summary

# ── 检查并停止已有进程 ────────────────────────────────────
for PORT in 8765 8766; do
  PID=$(lsof -ti :$PORT 2>/dev/null)
  if [ -n "$PID" ]; then
    echo "端口 ${PORT} 已被占用（PID ${PID}），先停掉..."
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
  if local_curl -s http://localhost:8766/health > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

# ── 启动异步 worker（jobs 表后台任务）──────────────────────
WORKER_PID_FILE="$LOG_DIR/worker.pid"
if [ -f "$WORKER_PID_FILE" ]; then
  OLD_WORKER_PID="$(cat "$WORKER_PID_FILE" 2>/dev/null)"
  if [ -n "$OLD_WORKER_PID" ] && ps -p "$OLD_WORKER_PID" > /dev/null 2>&1; then
    echo "worker 已在运行（PID ${OLD_WORKER_PID}），先停掉..."
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
if local_curl -s http://localhost:8765 > /dev/null 2>&1; then
  echo "✓ 知识库服务器：http://localhost:8765"
else
  echo "✗ 知识库服务器启动失败，查看日志：$LOG_DIR/server.log"
  ALL_OK=false
fi

if local_curl -s http://localhost:8766/health > /dev/null 2>&1; then
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
