#!/bin/sh
# mem-ai 模型服务选择脚本
# 用法：
#   sh choose_ai_service.sh              # 菜单模式
#   sh choose_ai_service.sh claude       # 切到 Claude Code
#   sh choose_ai_service.sh codex        # 切到 Codex CLI
#   sh choose_ai_service.sh qwen         # 配置千问 / Qwen API
#   sh choose_ai_service.sh openrouter   # 配置 OpenRouter API

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${KB_CONFIG_FILE:-$HOME/.kb_config}"
DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
DB_FILE="$DATA_DIR/comments.db"
LOG_DIR="$DATA_DIR/.logs"

echo "=== mem-ai 模型设置 ==="
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

check_background_jobs_before_switch() {
  echo ""
  echo "切换前检查后台任务..."

  if [ -f "$LOG_DIR/worker.pid" ]; then
    worker_pid="$(cat "$LOG_DIR/worker.pid" 2>/dev/null || true)"
    if [ -n "$worker_pid" ] && ps -p "$worker_pid" >/dev/null 2>&1; then
      echo "Worker：正在运行（PID $worker_pid）"
    else
      echo "Worker：未运行或 pid 已失效"
    fi
  else
    echo "Worker：未找到 pid 文件"
  fi

  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "未找到 sqlite3，跳过 jobs 队列检查。"
    return 0
  fi
  if [ ! -f "$DB_FILE" ]; then
    echo "未找到本地 DB：$DB_FILE，跳过 jobs 队列检查。"
    return 0
  fi

  active_count="$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running');" 2>/dev/null || echo 0)"
  case "$active_count" in
    ''|*[!0-9]*) active_count=0 ;;
  esac

  if [ "$active_count" -eq 0 ]; then
    echo "后台 jobs：没有排队或运行中的任务，可以切换。"
    return 0
  fi

  echo "⚠ 后台 jobs：发现 $active_count 个排队/运行中的任务。"
  sqlite3 -header -column "$DB_FILE" \
    "SELECT id, kind, status, created_at, started_at FROM jobs WHERE status IN ('queued','running') ORDER BY created_at LIMIT 10;" \
    2>/dev/null || true
  echo ""
  echo "这些任务可能还在使用切换前的模型服务。建议等它们完成后再切换。"
  echo "如果现在继续切换并重启，已 running 的任务可能要等 lease 过期后才会恢复重跑。"
  printf "仍然继续切换？[y/N] "
  read -r continue_switch
  case "$continue_switch" in
    y|Y|yes|YES)
      ;;
    *)
      echo "已取消切换。你可以稍后再运行：sh choose_ai_service.sh"
      exit 1
      ;;
  esac
}

prepare_switch() {
  check_background_jobs_before_switch
  backup_config
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

read_api_key() {
  echo ""
  echo "请粘贴 API Key 后按回车。为安全起见，粘贴时屏幕不会显示字符，这是正常的。"
  printf "API Key（不会显示，粘贴后按回车）："
  if [ -t 0 ]; then
    stty -echo 2>/dev/null || true
  fi
  read -r api_key
  if [ -t 0 ]; then
    stty echo 2>/dev/null || true
  fi
  echo ""
  if [ -z "$api_key" ]; then
    echo "没有收到 API Key。请确认终端窗口处于选中状态，然后重新运行脚本。"
    exit 1
  fi
  echo "已收到 API Key（长度 ${#api_key} 位，已隐藏，不会打印原文）。"
}

read_required() {
  prompt="$1"
  value=""
  while [ -z "$value" ]; do
    printf "%s：" "$prompt"
    read -r value
    if [ -z "$value" ]; then
      echo "这里不能为空。"
    fi
  done
}

save_api_config() {
  api_provider="$1"
  provider_label="$2"
  base_url="$3"
  model="$4"

  prepare_switch
  upsert_config "MEMAI_LLM_PROVIDER" "api"
  upsert_config "MEMAI_LLM_API_PROVIDER" "$api_provider"
  upsert_config "MEMAI_LOCAL_AGENT" "none"
  upsert_config "MEMAI_LLM_FALLBACK" "fail"
  upsert_config "MEMAI_LLM_API_KEY" "$api_key"
  upsert_config "MEMAI_LLM_BASE_URL" "$base_url"
  upsert_config "MEMAI_LLM_MODEL" "$model"
  chmod 600 "$CONFIG_FILE" 2>/dev/null || true

  echo ""
  echo "已切换为：${provider_label}"
  echo "Provider：$api_provider"
  echo "Base URL：$base_url"
  echo "Model：$model"
  restart_prompt
}

choose_qwen_endpoint() {
  echo "先选千问 API 类型。"
  echo ""
  echo "1) 阿里云百炼 / 中国内地标准 API  普通百炼 API Key 通常选这个"
  echo "2) Qwen Global / 新加坡标准 API   海外或 Global Key 选这个"
  echo "3) Qwen Coding Plan / 中国内地    只有 Coding Plan 订阅用户选"
  echo "4) Qwen Coding Plan / Global      只有 Coding Plan 订阅用户选"
  echo "5) 手动填写 Base URL"
  echo ""
  printf "输入 1 / 2 / 3 / 4 / 5 后回车 [1]："
  read -r qwen_endpoint_choice
  qwen_endpoint_choice="${qwen_endpoint_choice:-1}"

  case "$qwen_endpoint_choice" in
    1)
      qwen_endpoint_label="阿里云百炼 / 中国内地标准 API"
      base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
      ;;
    2)
      qwen_endpoint_label="Qwen Global / 新加坡标准 API"
      base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
      ;;
    3)
      qwen_endpoint_label="Qwen Coding Plan / 中国内地"
      base_url="https://coding.dashscope.aliyuncs.com/v1"
      ;;
    4)
      qwen_endpoint_label="Qwen Coding Plan / Global"
      base_url="https://coding-intl.dashscope.aliyuncs.com/v1"
      ;;
    5)
      qwen_endpoint_label="自定义 Qwen endpoint"
      read_required "Base URL"
      base_url="${value%/}"
      ;;
    *)
      echo "没看懂这个选择：$qwen_endpoint_choice"
      echo "为避免填错 endpoint，本次没有修改配置。"
      exit 1
      ;;
  esac

  echo "已选择：$qwen_endpoint_label"
  echo "Base URL：$base_url"
}

choose_qwen_model() {
  echo ""
  echo "再选默认模型。"
  echo ""
  echo "1) qwen3.5-plus  推荐默认，OpenClaw Qwen provider 默认模型"
  echo "2) qwen3.6-plus  更强；标准 API 更适合，Coding Plan 可能不支持"
  echo "3) qwen-plus     保守兼容；旧百炼项目遇到不支持时选这个"
  echo "4) 手动填写模型名"
  echo ""
  printf "输入 1 / 2 / 3 / 4 后回车 [1]："
  read -r qwen_model_choice
  qwen_model_choice="${qwen_model_choice:-1}"

  case "$qwen_model_choice" in
    1)
      model="qwen3.5-plus"
      ;;
    2)
      model="qwen3.6-plus"
      ;;
    3)
      model="qwen-plus"
      ;;
    4)
      read_required "Model"
      model="$value"
      ;;
    *)
      echo "没看懂这个模型选择：$qwen_model_choice"
      echo "为避免填错模型，本次没有修改配置。"
      exit 1
      ;;
  esac
}

set_qwen() {
  echo "切换为：千问 / Qwen API"
  echo "普通用户只需要按顺序选择 API 类型、粘贴 Key、选择模型。"
  echo ""
  choose_qwen_endpoint
  read_api_key
  choose_qwen_model
  save_api_config "qwen" "千问 / Qwen API（${qwen_endpoint_label}）" "$base_url" "$model"
}

choose_openrouter_model() {
  echo ""
  echo "OpenRouter 的模型名通常长这样：openai/gpt-4o-mini。"
  echo "如果不知道选哪个，直接回车用默认模型；如果你在 OpenRouter 网站复制了模型 ID，就选 2。"
  echo ""
  echo "1) openai/gpt-4o-mini  默认，便宜稳妥"
  echo "2) 手动填写 OpenRouter 模型 ID"
  echo ""
  printf "输入 1 / 2 后回车 [1]："
  read -r openrouter_model_choice
  openrouter_model_choice="${openrouter_model_choice:-1}"

  case "$openrouter_model_choice" in
    1)
      model="openai/gpt-4o-mini"
      ;;
    2)
      read_required "OpenRouter Model ID"
      model="$value"
      ;;
    *)
      echo "没看懂这个模型选择：$openrouter_model_choice"
      echo "为避免填错模型，本次没有修改配置。"
      exit 1
      ;;
  esac
}

set_openrouter() {
  echo "切换为：OpenRouter API"
  echo "你只需要粘贴 OpenRouter API Key；Base URL 会自动使用 OpenRouter 官方地址。"
  echo ""
  read_api_key
  choose_openrouter_model
  save_api_config "openrouter" "OpenRouter API" "https://openrouter.ai/api/v1" "$model"
}

set_codex() {
  codex_bin="$(find_bin codex "$HOME/.npm-global/bin/codex" "/usr/local/bin/codex" "/opt/homebrew/bin/codex")"
  if [ -z "$codex_bin" ]; then
    echo "未找到 Codex CLI。请先安装并登录 Codex，然后重新运行：sh choose_ai_service.sh"
    exit 1
  fi
  prepare_switch
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
  prepare_switch
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

mode="${1:-}"
if [ -z "$mode" ]; then
  echo "请选择默认模型服务："
  echo ""
  echo "1) Claude Code 直连   已安装并登录 Claude Code 的用户选这个"
  echo "2) Codex 直连         已安装并登录 Codex CLI 的用户选这个"
  echo "3) 千问 / Qwen API    有阿里云百炼或 Qwen API Key 的用户选这个"
  echo "4) OpenRouter API     有 OpenRouter API Key 的用户选这个"
  echo ""
  printf "输入 1 / 2 / 3 / 4 后回车："
  read -r mode
fi

case "$mode" in
  1|claude|claude_code|Claude|CLAUDE)
    set_claude
    ;;
  2|codex|codex_cli|Codex|CODEX)
    set_codex
    ;;
  3|qwen|Qwen|QWEN|dashscope|DashScope|千问|百炼)
    set_qwen
    ;;
  4|openrouter|OpenRouter|OPENROUTER)
    set_openrouter
    ;;
  api|API)
    echo "请直接选择具体服务商："
    echo "3) 千问 / Qwen API"
    echo "4) OpenRouter API"
    exit 1
    ;;
  *)
    echo "没看懂这个选择：$mode"
    echo "请运行 sh choose_ai_service.sh，然后输入 1、2、3 或 4。"
    exit 1
    ;;
esac
