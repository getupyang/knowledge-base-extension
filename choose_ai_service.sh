#!/bin/sh
# mem-ai AI 服务选择脚本
# 用法：
#   sh choose_ai_service.sh          # 菜单模式
#   sh choose_ai_service.sh codex    # 直接切到 Codex CLI
#   sh choose_ai_service.sh claude   # 直接切到 Claude Code
#   sh choose_ai_service.sh api      # 直接切到自有 API

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${KB_CONFIG_FILE:-$HOME/.kb_config}"

echo "=== mem-ai AI 后端设置 ==="
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
  echo "未找到配置文件：$CONFIG_FILE"
  echo "请先在产品文件夹里运行首次安装：sh setup.sh"
  exit 1
fi

find_bin() {
  name="$1"
  shift
  for p in "$@" "$(command -v "$name" 2>/dev/null || true)"; do
    if [ -n "$p" ] && [ -x "$p" ]; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

upsert_config() {
  key="$1"
  value="$2"
  tmp="$(mktemp "${TMPDIR:-/tmp}/memai-config.XXXXXX")" || exit 1
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) print key "=" value
    }
  ' "$CONFIG_FILE" > "$tmp" && mv "$tmp" "$CONFIG_FILE"
}

backup_config() {
  backup="$CONFIG_FILE.bak.$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG_FILE" "$backup"
  chmod 600 "$CONFIG_FILE" "$backup" 2>/dev/null || true
  echo "已备份原配置：$backup"
}

restart_prompt() {
  echo ""
  printf "是否现在重启服务，让新设置立刻生效？[Y/n] "
  read -r answer
  case "$answer" in
    n|N|no|NO)
      echo "已保存配置。之后运行 sh start.sh 生效。"
      ;;
    *)
      echo "正在重启服务..."
      sh "$REPO_DIR/start.sh"
      ;;
  esac
}

set_codex() {
  codex_bin="$(find_bin codex "$HOME/.npm-global/bin/codex" "/usr/local/bin/codex" "/opt/homebrew/bin/codex")"
  if [ -z "$codex_bin" ]; then
    echo "未找到 Codex CLI。请先安装并登录 Codex，然后重新运行：sh choose_ai_service.sh"
    exit 1
  fi
  backup_config
  upsert_config "MEMAI_LLM_PROVIDER" "codex_cli"
  upsert_config "MEMAI_LOCAL_AGENT" "codex_cli"
  upsert_config "MEMAI_LLM_FALLBACK" "fail"
  upsert_config "MEMAI_CODEX_BIN" "$codex_bin"
  upsert_config "MEMAI_CODEX_SANDBOX" "read-only"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
  echo ""
  echo "已切换为：Codex 直连"
  echo "使用路径：$codex_bin"
  restart_prompt
}

set_claude() {
  claude_bin="$(find_bin claude "$HOME/.npm-global/bin/claude" "/usr/local/bin/claude" "/opt/homebrew/bin/claude")"
  if [ -z "$claude_bin" ]; then
    echo "未找到 Claude Code。请先安装并登录 Claude Code，然后重新运行：sh choose_ai_service.sh"
    exit 1
  fi
  backup_config
  upsert_config "MEMAI_LLM_PROVIDER" "claude_code"
  upsert_config "MEMAI_LOCAL_AGENT" "claude_code"
  upsert_config "MEMAI_LLM_FALLBACK" "fail"
  upsert_config "CLAUDE_BIN" "$claude_bin"
  upsert_config "MEMAI_CLAUDE_BIN" "$claude_bin"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
  echo ""
  echo "已切换为：Claude Code 直连"
  echo "使用路径：$claude_bin"
  restart_prompt
}

set_api() {
  echo "切换为：自己的 API"
  echo "支持 OpenRouter / OpenAI / DeepSeek / Kimi 等 OpenAI-compatible 接口。"
  echo ""
  printf "API Key: "
  stty -echo 2>/dev/null || true
  read -r api_key
  stty echo 2>/dev/null || true
  echo ""
  if [ -z "$api_key" ]; then
    echo "API Key 不能为空。"
    exit 1
  fi
  printf "Base URL [https://openrouter.ai/api/v1]: "
  read -r base_url
  base_url="${base_url:-https://openrouter.ai/api/v1}"
  printf "Model [openai/gpt-4o-mini]: "
  read -r model
  model="${model:-openai/gpt-4o-mini}"

  backup_config
  upsert_config "MEMAI_LLM_PROVIDER" "api"
  upsert_config "MEMAI_LOCAL_AGENT" "none"
  upsert_config "MEMAI_LLM_FALLBACK" "fail"
  upsert_config "MEMAI_LLM_API_KEY" "$api_key"
  upsert_config "MEMAI_LLM_BASE_URL" "$base_url"
  upsert_config "MEMAI_LLM_MODEL" "$model"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true
  echo ""
  echo "已切换为：自己的 API"
  echo "Base URL：$base_url"
  echo "Model：$model"
  restart_prompt
}

mode="${1:-}"
if [ -z "$mode" ]; then
  echo "请选择你想用哪种 AI："
  echo ""
  echo "1) Codex 直连       已安装并登录 Codex 的用户选这个"
  echo "2) Claude Code 直连 已安装并登录 Claude Code 的用户选这个"
  echo "3) 自己的 API       有 OpenRouter / OpenAI / DeepSeek / Kimi API Key 的用户选这个"
  echo ""
  printf "输入 1 / 2 / 3 后回车："
  read -r mode
fi

case "$mode" in
  1|codex|codex_cli|Codex|CODEX)
    set_codex
    ;;
  2|claude|claude_code|Claude|CLAUDE)
    set_claude
    ;;
  3|api|API|openai|openrouter)
    set_api
    ;;
  *)
    echo "没看懂这个选择：$mode"
    echo "请运行 sh choose_ai_service.sh，然后输入 1、2 或 3。"
    exit 1
    ;;
esac
