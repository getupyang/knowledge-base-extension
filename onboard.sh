#!/bin/bash
# ═══════════════════════════════════════════════════════
# Margin — 首次 onboarding 脚本
# 用法：bash onboard.sh
# ═══════════════════════════════════════════════════════

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
CONFIG_FILE="$HOME/.kb_config"
if [ -t 1 ]; then
  GREEN="$(printf '\033[32m')"
  RESET="$(printf '\033[0m')"
else
  GREEN=""
  RESET=""
fi

echo "=== Margin Onboarding ==="
echo ""

# ── 1. 检查依赖 ──────────────────────────────────────────
echo "→ 检查运行环境..."

check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "  ✗ 缺少：$1（$2）"
    return 1
  else
    echo "  ✓ $1"
    return 0
  fi
}

DEPS_OK=true
check_cmd python3 "请安装 Python 3：https://python.org" || DEPS_OK=false

NODE_VERSION=""
if command -v node &>/dev/null; then
  if NODE_VERSION="$(node --version 2>/dev/null)"; then
    echo "  ✓ node 可用（可选）"
  else
    echo "  ○ node 已安装但当前不可运行（可选；不影响第一次使用）"
  fi
else
  echo "  ○ node 未找到（可选；不影响第一次使用）"
fi

CLAUDE_BIN=""
for p in \
  "$HOME/.npm-global/bin/claude" \
  "/usr/local/bin/claude" \
  "/opt/homebrew/bin/claude" \
  "$(which claude 2>/dev/null)"; do
  if [ -f "$p" ] && [ -x "$p" ]; then
    CLAUDE_BIN="$p"
    break
  fi
done

if [ -z "$CLAUDE_BIN" ]; then
  echo "  ○ Claude Code 未找到（可选；不影响用 API Key 配置 AI 服务）"
else
  echo "  ✓ Claude Code 已安装 ($CLAUDE_BIN)"
fi

CODEX_BIN=""
for p in \
  "$HOME/.npm-global/bin/codex" \
  "/usr/local/bin/codex" \
  "/opt/homebrew/bin/codex" \
  "$(which codex 2>/dev/null)"; do
  if [ -f "$p" ] && [ -x "$p" ]; then
    CODEX_BIN="$p"
    break
  fi
done

if [ -z "$CODEX_BIN" ]; then
  echo "  ○ Codex CLI 未找到（可选；不影响用 API Key 配置 AI 服务）"
else
  echo "  ✓ Codex CLI 已安装 ($CODEX_BIN)"
fi

if [ "$DEPS_OK" = false ]; then
  echo ""
  echo "请先安装上面缺少的运行环境，然后重新运行此脚本。"
  exit 1
fi

choose_qwen_endpoint_for_setup() {
  echo ""
  echo "  先确认你的千问 Key 来自哪里。"
  echo ""
  echo "  1) 阿里云百炼 / 中国内地标准 API  普通百炼 API Key 通常选这个"
  echo "  2) Qwen Global / 新加坡标准 API   海外或 Global Key 选这个"
  echo "  3) Qwen Coding Plan / 中国内地    只有 Coding Plan 订阅用户选"
  echo "  4) Qwen Coding Plan / Global      只有 Coding Plan 订阅用户选"
  echo "  5) 手动填写服务地址（高级）"
  echo ""
  read -p "  输入 1 / 2 / 3 / 4 / 5 后回车 [1]：" QWEN_ENDPOINT_CHOICE
  QWEN_ENDPOINT_CHOICE="${QWEN_ENDPOINT_CHOICE:-1}"
  case "$QWEN_ENDPOINT_CHOICE" in
    1)
      API_PROVIDER_LABEL="千问 / Qwen API（阿里云百炼 / 中国内地标准 API）"
      API_BASE_URL_DEFAULT="https://dashscope.aliyuncs.com/compatible-mode/v1"
      ;;
    2)
      API_PROVIDER_LABEL="千问 / Qwen API（Qwen Global / 新加坡标准 API）"
      API_BASE_URL_DEFAULT="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
      ;;
    3)
      API_PROVIDER_LABEL="千问 / Qwen API（Qwen Coding Plan / 中国内地）"
      API_BASE_URL_DEFAULT="https://coding.dashscope.aliyuncs.com/v1"
      ;;
    4)
      API_PROVIDER_LABEL="千问 / Qwen API（Qwen Coding Plan / Global）"
      API_BASE_URL_DEFAULT="https://coding-intl.dashscope.aliyuncs.com/v1"
      ;;
    5)
      API_PROVIDER_LABEL="千问 / Qwen API（自定义服务地址）"
      API_BASE_URL_DEFAULT=""
      ;;
    *)
      echo "  ✗ 请输入上面列出的编号。"
      exit 1
      ;;
  esac
}

choose_qwen_model_for_setup() {
  echo ""
  echo "  再选默认模型。"
  echo ""
  echo "  1) qwen3.5-plus  推荐默认，兼顾质量和稳定性"
  echo "  2) qwen3.6-plus  更强；普通百炼 API 更适合，Coding Plan 可能不支持"
  echo "  3) qwen-plus     保守兼容；旧百炼项目遇到不支持时选这个"
  echo "  4) 手动填写模型名"
  echo ""
  read -p "  输入 1 / 2 / 3 / 4 后回车 [1]：" QWEN_MODEL_CHOICE
  QWEN_MODEL_CHOICE="${QWEN_MODEL_CHOICE:-1}"

  case "$QWEN_MODEL_CHOICE" in
    1)
      LLM_MODEL="qwen3.5-plus"
      ;;
    2)
      LLM_MODEL="qwen3.6-plus"
      ;;
    3)
      LLM_MODEL="qwen-plus"
      ;;
    4)
      read_with_default_for_setup "模型名" "" LLM_MODEL
      ;;
    *)
      echo "  ✗ 请输入上面列出的编号。"
      exit 1
      ;;
  esac
}

choose_openrouter_model_for_setup() {
  echo ""
  echo "  OpenRouter 的模型名通常长这样：openai/gpt-4o-mini。"
  echo "  如果不知道选哪个，直接回车用默认模型；如果你在 OpenRouter 网站复制了模型 ID，就选 2。"
  echo ""
  echo "  1) openai/gpt-4o-mini  默认，便宜稳妥"
  echo "  2) 手动填写 OpenRouter 模型 ID"
  echo ""
  read -p "  输入 1 / 2 后回车 [1]：" OPENROUTER_MODEL_CHOICE
  OPENROUTER_MODEL_CHOICE="${OPENROUTER_MODEL_CHOICE:-1}"

  case "$OPENROUTER_MODEL_CHOICE" in
    1)
      LLM_MODEL="openai/gpt-4o-mini"
      ;;
    2)
      read_with_default_for_setup "OpenRouter Model ID" "" LLM_MODEL
      ;;
    *)
      echo "  ✗ 请输入上面列出的编号。"
      exit 1
      ;;
  esac
}

choose_api_preset() {
  echo "  请选择你准备用哪个 API Key："
  echo ""
  echo "  1) 千问 / Qwen API    有阿里云百炼或 Qwen API Key 的用户选这个"
  echo "  2) OpenRouter API     有 OpenRouter API Key 的用户选这个"
  echo ""
  read -p "  输入 1 / 2 后回车 [1]：" API_CHOICE
  API_CHOICE="${API_CHOICE:-1}"

  case "$API_CHOICE" in
    1|qwen|Qwen|QWEN|dashscope|DashScope|bailian|Bailian|千问|百炼)
      LLM_API_PROVIDER="qwen"
      choose_qwen_endpoint_for_setup
      ;;
    2|openrouter|OpenRouter)
      LLM_API_PROVIDER="openrouter"
      API_PROVIDER_LABEL="OpenRouter API"
      API_BASE_URL_DEFAULT="https://openrouter.ai/api/v1"
      ;;
    *)
      echo "  ✗ 请输入上面列出的编号。"
      exit 1
      ;;
  esac
}

read_api_key_for_setup() {
  echo ""
  echo "  请粘贴 API Key 后按回车。为安全起见，粘贴时屏幕不会显示字符，这是正常的。"
  read -s -p "  API Key（不会显示，粘贴后按回车）：" LLM_API_KEY
  echo ""
  if [ -z "$LLM_API_KEY" ]; then
    echo "  ✗ 没有收到 API Key。请确认终端窗口处于选中状态，然后重新运行脚本。"
    exit 1
  fi
  echo "  ✓ 已收到 API Key（长度 ${#LLM_API_KEY} 位，已隐藏，不会打印原文）"
}

read_optional_secret_for_setup() {
  local prompt="$1"
  local result_var="$2"
  local entered_value=""
  read -s -p "  ${prompt}" entered_value
  echo ""
  printf -v "$result_var" "%s" "$entered_value"
}

configure_claude_auth_for_setup() {
  ANTHROPIC_API_KEY_FOR_CONFIG="${ANTHROPIC_API_KEY:-}"
  ANTHROPIC_BASE_URL_FOR_CONFIG=""
  ANTHROPIC_AUTH_TOKEN_FOR_CONFIG=""
  CLAUDE_CODE_OAUTH_TOKEN_FOR_CONFIG=""

  echo ""
  echo "  请选择 Claude Code 怎么登录："
  echo ""
  echo "  1) 使用这台电脑上已经登录的 Claude Code  推荐；不需要再输入 Key"
  echo "  2) 输入 Anthropic API Key                只有你平时就用这个 Key 跑 Claude Code 时选"
  echo ""
  read -p "  输入 1 / 2 后回车 [1]：" CLAUDE_AUTH_CHOICE
  CLAUDE_AUTH_CHOICE="${CLAUDE_AUTH_CHOICE:-1}"

  case "$CLAUDE_AUTH_CHOICE" in
    1)
      echo "  正在测试这台电脑上的 Claude Code..."
      if ! run_claude_probe_for_setup account; then
        print_claude_probe_failure_for_setup
        echo "  ✗ 这台电脑上的 Claude Code 暂时不能用，本次没有完成 AI 配置。"
        echo "  请先确认这个命令在终端里能跑通，然后重新运行 setup："
        echo "    claude -p \"Reply with exactly OK.\" --output-format json"
        exit 1
      fi
      ANTHROPIC_API_KEY_FOR_CONFIG=""
      echo "  ✓ Claude Code 可以使用，不需要保存 API Key。"
      ;;
    2)
      if [ -n "$ANTHROPIC_API_KEY_FOR_CONFIG" ]; then
        echo "  已检测到当前终端的 ANTHROPIC_API_KEY（长度 ${#ANTHROPIC_API_KEY_FOR_CONFIG} 位，已隐藏）。"
      else
        echo "  请粘贴 ANTHROPIC_API_KEY。为安全起见，粘贴时屏幕不会显示字符。"
        read_optional_secret_for_setup "ANTHROPIC_API_KEY：" ANTHROPIC_API_KEY_FOR_CONFIG
      fi
      if [ -z "$ANTHROPIC_API_KEY_FOR_CONFIG" ]; then
        echo "  ✗ 没有收到 ANTHROPIC_API_KEY，本次没有完成 AI 配置。"
        exit 1
      fi
      ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_FOR_CONFIG"
      export ANTHROPIC_API_KEY
      echo "  正在测试这个 Claude API Key..."
      if ! run_claude_probe_for_setup api_key; then
        print_claude_probe_failure_for_setup
        echo "  ✗ 这个 Claude API Key 暂时不能用，本次没有保存。"
        exit 1
      fi
      echo "  ✓ 这个 Claude API Key 可以使用，会保存到本机配置（长度 ${#ANTHROPIC_API_KEY_FOR_CONFIG} 位，已隐藏）。"
      ;;
    *)
      echo "  ✗ 请输入上面列出的编号。"
      exit 1
      ;;
  esac
}

run_claude_probe_for_setup() {
  local probe_mode="${1:-current}"
  CLAUDE_PROBE_DETAIL=""
  local probe_out
  probe_out="$(mktemp "${TMPDIR:-/tmp}/memai-claude-probe.XXXXXX")" || return 1
  case "$probe_mode" in
    account)
      env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u CLAUDE_CODE_OAUTH_TOKEN \
        "$CLAUDE_BIN" -p "Reply with exactly OK." --output-format json --dangerously-skip-permissions > "$probe_out" 2>&1
      ;;
    api_key)
      env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        "$CLAUDE_BIN" -p "Reply with exactly OK." --output-format json --dangerously-skip-permissions > "$probe_out" 2>&1
      ;;
    *)
      "$CLAUDE_BIN" -p "Reply with exactly OK." --output-format json --dangerously-skip-permissions > "$probe_out" 2>&1
      ;;
  esac
  local probe_status=$?
  if [ "$probe_status" -ne 0 ]; then
    CLAUDE_PROBE_DETAIL="$(sed -n '1,12p' "$probe_out" 2>/dev/null)"
  elif grep -Eq '"is_error"[[:space:]]*:[[:space:]]*true|"api_error_status"[[:space:]]*:' "$probe_out" 2>/dev/null; then
    CLAUDE_PROBE_DETAIL="$(sed -n '1,12p' "$probe_out" 2>/dev/null)"
    probe_status=1
  fi
  rm -f "$probe_out"
  return "$probe_status"
}

print_claude_probe_failure_for_setup() {
  echo "  Claude Code 自动测试没有通过。"
  case "$CLAUDE_PROBE_DETAIL" in
    *401*|*Unauthorized*|*unauthorized*|*api_error_status*)
      echo "  看起来是认证失败（401）。如果你平时用 API Key 跑 claude，请在下一步粘贴同一个 key。"
      ;;
    "")
      echo "  没有拿到错误详情。"
      ;;
    *)
      echo "  错误摘要："
      printf '%s\n' "$CLAUDE_PROBE_DETAIL"
      ;;
  esac
}

read_with_default_for_setup() {
  local prompt="$1"
  local default_value="$2"
  local result_var="$3"
  local entered_value=""
  if [ -n "$default_value" ]; then
    read -p "  $prompt [$default_value]：" entered_value
    entered_value="${entered_value:-$default_value}"
  else
    while [ -z "$entered_value" ]; do
      read -p "  ${prompt}：" entered_value
      if [ -z "$entered_value" ]; then
        echo "  这里不能为空。"
      fi
    done
  fi
  printf -v "$result_var" "%s" "$entered_value"
}

configure_api_settings() {
  LLM_API_PROVIDER=""
  LLM_API_KEY=""
  LLM_BASE_URL=""
  LLM_MODEL=""

  choose_api_preset
  read_api_key_for_setup
  if [ -n "$API_BASE_URL_DEFAULT" ]; then
    LLM_BASE_URL="$API_BASE_URL_DEFAULT"
    echo "  ✓ 已选择服务地址：$LLM_BASE_URL"
  else
    read_with_default_for_setup "服务地址 Base URL" "" LLM_BASE_URL
  fi
  LLM_BASE_URL="${LLM_BASE_URL%/}"
  if [ "$LLM_API_PROVIDER" = "qwen" ]; then
    choose_qwen_model_for_setup
  else
    choose_openrouter_model_for_setup
  fi
  validate_api_settings_for_setup
  echo "  ✓ 已配置：$API_PROVIDER_LABEL / $LLM_MODEL"
}

validate_api_settings_for_setup() {
  echo "  正在验证 API 连接（会发送一条极小测试请求）..."
  API_PROBE_DETAIL=""
  local probe_out
  probe_out="$(mktemp "${TMPDIR:-/tmp}/memai-api-probe.XXXXXX")" || return 1

  if MEMAI_LLM_API_PROVIDER="$LLM_API_PROVIDER" \
    MEMAI_LLM_API_KEY="$LLM_API_KEY" \
    MEMAI_LLM_BASE_URL="$LLM_BASE_URL" \
    MEMAI_LLM_MODEL="$LLM_MODEL" \
    python3 - <<'PY' > "$probe_out" 2>&1
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

base_url = os.environ["MEMAI_LLM_BASE_URL"].rstrip("/")
api_key = os.environ["MEMAI_LLM_API_KEY"]
model = os.environ["MEMAI_LLM_MODEL"]
provider = os.environ.get("MEMAI_LLM_API_PROVIDER", "api")
url = f"{base_url}/chat/completions"

payload = {
    "model": model,
    "messages": [{"role": "user", "content": "Reply with exactly OK."}],
    "temperature": 0,
    "max_tokens": 8,
}
request = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "knowledge-base-extension-setup/1.0",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read(4096)
except urllib.error.HTTPError as exc:
    body = exc.read(800).decode("utf-8", errors="replace")
    if exc.code == 401:
        print("API 验证失败：401 认证失败。通常是 API Key、所选服务或地区不匹配。")
        if provider == "qwen":
            print("请确认这个 key 属于你刚选择的百炼/Global/Coding Plan 类型，然后重新运行 setup。")
        else:
            print("请确认 API Key 属于刚选择的服务商，然后重新运行 setup。")
    elif exc.code == 404:
        print("API 验证失败：404。通常是服务地址或模型名不正确。")
    elif exc.code == 429:
        print("API 验证失败：429。服务商返回限流或额度不足。")
    else:
        print(f"API 验证失败：HTTP {exc.code}。")
    if body:
        print(body[:800])
    sys.exit(1)
except urllib.error.URLError as exc:
    reason = exc.reason
    text = str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        print("API 验证失败：Python 无法验证 HTTPS 证书。")
        print("macOS python.org 版本通常需要运行：/Applications/Python 3.x/Install Certificates.command")
    else:
        print(f"API 验证失败：网络连接失败：{text}")
    sys.exit(1)
except Exception as exc:
    print(f"API 验证失败：{type(exc).__name__}: {exc}")
    sys.exit(1)

print("API 验证通过")
PY
  then
    echo "  ✓ API 连接验证通过"
    rm -f "$probe_out"
    return 0
  fi

  API_PROBE_DETAIL="$(sed -n '1,12p' "$probe_out" 2>/dev/null)"
  rm -f "$probe_out"
  echo "  ✗ API 连接验证未通过，本次不会继续写入这组模型配置。"
  if [ -n "$API_PROBE_DETAIL" ]; then
    printf '%s\n' "$API_PROBE_DETAIL"
  fi
  exit 1
}

# ── 2. 安装 Python 依赖 ───────────────────────────────────
echo ""
echo "→ 准备运行环境..."
if pip3 install -q -r "$REPO_DIR/requirements.txt"; then
  echo "  ✓ 运行环境就绪"
else
  echo "  ✗ 运行环境准备失败。"
  echo "  常见原因：网络不可用、代理无法连接，或者 Python 证书没有配置好。"
  echo "  请解决上面的 pip 错误后，重新运行 bash onboard.sh。"
  exit 1
fi

# ── 3. 配置密钥 ───────────────────────────────────────────
echo ""
echo "→ 准备本机资料库..."
echo ""
echo "  你的划线、评论和 AI 生成的内容会先保存在这台电脑上。"
echo "  数据位置：$DATA_DIR/comments.db（通常不用手动打开）"
echo ""

EXISTING_NOTION_TOKEN=""
EXISTING_NOTION_DATABASE_ID=""
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE" 2>/dev/null || true
  EXISTING_NOTION_TOKEN="${NOTION_TOKEN:-}"
  EXISTING_NOTION_DATABASE_ID="${NOTION_DATABASE_ID:-}"
  echo "  检测到这台电脑已经配置过：$CONFIG_FILE"
  read -p "  是否重新配置？[y/N] " RECONFIG
  if [ "$RECONFIG" != "y" ] && [ "$RECONFIG" != "Y" ]; then
    echo "  跳过配置步骤。"
  else
    rm "$CONFIG_FILE"
  fi
fi

if [ ! -f "$CONFIG_FILE" ]; then
  NOTION_TOKEN="$EXISTING_NOTION_TOKEN"
  DATABASE_ID="$EXISTING_NOTION_DATABASE_ID"
  if [ -n "$NOTION_TOKEN" ] && [ -n "$DATABASE_ID" ]; then
    echo "  ✓ 已保留你之前的云端备份配置；本次不重新询问。"
  else
    echo "  ✓ 数据均存储在本地。如需云端数据，后续可点击插件配置你的notion云端数据库。"
  fi

  echo ""
  echo "→ 配置 AI 服务..."
  echo "  请选择你想用的 LLM 模型。"
  echo ""
  if [ -z "$CLAUDE_BIN" ] && [ -z "$CODEX_BIN" ]; then
    echo "  这台电脑还没安装 Claude Code / Codex，需要先填一个 API Key。"
    configure_api_settings
  else
    LLM_API_PROVIDER=""
    LLM_API_KEY=""
    LLM_BASE_URL=""
    LLM_MODEL=""
    echo "  已检测到这台电脑安装了 Claude Code / Codex。"
    echo "  你可以直接使用它；也可以额外填一个 API Key 作为备用方式。"
    echo "  如果现在跳过，只会用 Claude Code / Codex，不会调用付费 API。"
    read -p "  是否现在填写 API Key？[y/N] " CONFIGURE_API
    if [ "$CONFIGURE_API" = "y" ] || [ "$CONFIGURE_API" = "Y" ]; then
      configure_api_settings
    else
      echo "  已跳过 API Key。之后可运行 sh choose_ai_service.sh 切换。"
    fi
  fi

  if [ -z "$LLM_API_KEY" ] && [ -z "$CLAUDE_BIN" ] && [ -z "$CODEX_BIN" ]; then
    echo "  ✗ 还没有可用的 AI 服务。请填写 API Key，或先安装 Claude Code / Codex CLI。"
    exit 1
  fi

  AVAILABLE_PROVIDERS=()
  [ -n "$CLAUDE_BIN" ] && AVAILABLE_PROVIDERS+=("claude_code")
  [ -n "$CODEX_BIN" ] && AVAILABLE_PROVIDERS+=("codex_cli")
  [ -n "$LLM_API_KEY" ] && AVAILABLE_PROVIDERS+=("api")

  if [ "${#AVAILABLE_PROVIDERS[@]}" -eq 1 ]; then
    LLM_PROVIDER="${AVAILABLE_PROVIDERS[0]}"
  else
    echo ""
    echo "  检测到多个可用的 AI 服务，请选择默认使用哪一个。之后如需切换，运行 sh choose_ai_service.sh。"
    while true; do
      MODEL_OPTION_LABELS=()
      MODEL_OPTION_VALUES=()
      if [ -n "$CLAUDE_BIN" ]; then
        MODEL_OPTION_LABELS+=("Claude Code 直连")
        MODEL_OPTION_VALUES+=("claude_code")
      fi
      if [ -n "$CODEX_BIN" ]; then
        MODEL_OPTION_LABELS+=("Codex 直连")
        MODEL_OPTION_VALUES+=("codex_cli")
      fi
      if [ -n "$LLM_API_KEY" ]; then
        MODEL_OPTION_LABELS+=("${API_PROVIDER_LABEL} / ${LLM_MODEL}")
        MODEL_OPTION_VALUES+=("api")
      fi

      for i in "${!MODEL_OPTION_VALUES[@]}"; do
        echo "  $((i + 1))) ${MODEL_OPTION_LABELS[$i]}"
      done
      read -p "  输入编号后回车：" MODEL_PROVIDER_CHOICE
      if [[ "$MODEL_PROVIDER_CHOICE" =~ ^[0-9]+$ ]] && [ "$MODEL_PROVIDER_CHOICE" -ge 1 ] && [ "$MODEL_PROVIDER_CHOICE" -le "${#MODEL_OPTION_VALUES[@]}" ]; then
        LLM_PROVIDER="${MODEL_OPTION_VALUES[$((MODEL_PROVIDER_CHOICE - 1))]}"
        break
      fi
      echo "  请输入上面列出的编号。"
    done
  fi

  if [ "$LLM_PROVIDER" = "api" ]; then
    LOCAL_AGENT="none"
    LLM_FALLBACK="fail"
  else
    LOCAL_AGENT="$LLM_PROVIDER"
    # 防止本地订阅用户不知情地产生 API 成本；需要时手动改成 api。
    LLM_FALLBACK="fail"
  fi
  if [ "$LLM_PROVIDER" = "claude_code" ]; then
    configure_claude_auth_for_setup
  else
    ANTHROPIC_API_KEY_FOR_CONFIG="${ANTHROPIC_API_KEY:-}"
    ANTHROPIC_BASE_URL_FOR_CONFIG=""
    ANTHROPIC_AUTH_TOKEN_FOR_CONFIG=""
    CLAUDE_CODE_OAUTH_TOKEN_FOR_CONFIG=""
  fi

  cat > "$CONFIG_FILE" << EOF
# Margin 配置文件
# 修改后需要重新运行 start.sh

NOTION_TOKEN=${NOTION_TOKEN}
NOTION_DATABASE_ID=${DATABASE_ID}
MEMAI_LOCAL_BACKUP_ENABLED=1
# 本地恢复快照数量；不会删除你的主资料库。
MEMAI_BACKUP_KEEP=14
MEMAI_LLM_PROVIDER=${LLM_PROVIDER}
MEMAI_LOCAL_AGENT=${LOCAL_AGENT}
MEMAI_LLM_FALLBACK=${LLM_FALLBACK}
MEMAI_LLM_API_PROVIDER=${LLM_API_PROVIDER}
MEMAI_LLM_API_KEY=${LLM_API_KEY}
MEMAI_LLM_BASE_URL=${LLM_BASE_URL}
MEMAI_LLM_MODEL=${LLM_MODEL}
CLAUDE_BIN=${CLAUDE_BIN}
MEMAI_CLAUDE_BIN=${CLAUDE_BIN}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY_FOR_CONFIG}
ANTHROPIC_AUTH_TOKEN=${ANTHROPIC_AUTH_TOKEN_FOR_CONFIG}
ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL_FOR_CONFIG}
CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN_FOR_CONFIG}
MEMAI_CODEX_BIN=${CODEX_BIN}
MEMAI_CODEX_SANDBOX=read-only
KB_DATA_DIR=${DATA_DIR}
RESEARCH_DIR=${BACKEND_DIR}
EOF

  chmod 600 "$CONFIG_FILE"
  echo "  ✓ 配置已保存到 $CONFIG_FILE"
fi

# ── 4. 初始化数据库 ──────────────────────────────────────
echo ""
echo "→ 创建本机资料库..."
mkdir -p "$DATA_DIR"
python3 -c "
import sqlite3, os
db = os.path.join('$DATA_DIR', 'comments.db')
conn = sqlite3.connect(db)
conn.execute('''CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notion_page_id TEXT,
    page_url TEXT NOT NULL, page_title TEXT, selected_text TEXT,
    comment TEXT NOT NULL, agent_type TEXT, status TEXT DEFAULT \"open\",
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
conn.execute('''CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id INTEGER NOT NULL, author TEXT NOT NULL,
    agent_type TEXT, content TEXT NOT NULL, created_at TEXT NOT NULL,
    debug_meta TEXT, FOREIGN KEY (comment_id) REFERENCES comments(id))''')
try:
    conn.execute('ALTER TABLE replies ADD COLUMN debug_meta TEXT')
except: pass
try:
    conn.execute('ALTER TABLE comments ADD COLUMN notion_page_id TEXT')
except: pass
conn.commit()
conn.close()
print('  ✓ 本机资料库就绪:', db)
"

# ── 5. 初始化用户私有上下文 ─────────────────────────
if [ ! -f "$DATA_DIR/project_context.md" ]; then
  cat > "$DATA_DIR/project_context.md" << 'EOF'
# 项目上下文

（空白，用户还没有填写项目背景。AI 不能假设用户正在做某个项目。）

EOF
  echo "→ 已准备项目背景文件（可选以后补充）：$DATA_DIR/project_context.md"
fi

if [ ! -f "$DATA_DIR/user_profile.md" ]; then
  cat > "$DATA_DIR/user_profile.md" << 'EOF'
# 用户画像

（空白，系统会根据这台电脑上的本地批注逐步学习。）

EOF
  echo "→ 已准备用户偏好文件：$DATA_DIR/user_profile.md"
fi

if [ ! -f "$DATA_DIR/learned_rules.json" ]; then
  printf '{\n  "rules": []\n}\n' > "$DATA_DIR/learned_rules.json"
  echo "→ 已准备学习记录文件：$DATA_DIR/learned_rules.json"
fi

# ── 6. 记录数据存储状态 ───────────────────────────────────
echo ""
echo "→ 数据存储..."
source "$CONFIG_FILE" 2>/dev/null || true

if [ -n "$NOTION_TOKEN" ] && [ -n "$NOTION_DATABASE_ID" ]; then
  echo "  ✓ 已保留你之前的云端备份配置；本次安装不做云端验证"
else
  echo "  ✓ 当前只启用本机保存；"
fi

# ── 7. 可选：开机后自动可用 ────────────────────────────────
AUTO_START_ENABLED=false
STARTED_NOW=false
START_OUTPUT=""
SETUP_PREVIEW_MODE=false
case "$HOME:$DATA_DIR" in
  /tmp/kb-setup-home.*:*|*:*/tmp/kb-setup-data.*)
    SETUP_PREVIEW_MODE=true
    ;;
esac
if [ "$SETUP_PREVIEW_MODE" != true ]; then
  echo ""
  echo "→ 开机后自动可用..."
  if [ "$(uname -s)" = "Darwin" ] && [ -x "$REPO_DIR/scripts/install-launch-agent" ]; then
    echo "  开启开机自动启动，避免重启mac后手动运行bash start.sh来启动本产品。"
    read -p "  是否开启？[Y/n]：" ENABLE_AUTO_START
    ENABLE_AUTO_START="${ENABLE_AUTO_START:-Y}"
    case "$ENABLE_AUTO_START" in
      Y|y|yes|YES|Yes|是|好|开启)
        LAUNCH_AGENT_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/memai-launch-agent.XXXXXX")" || LAUNCH_AGENT_OUTPUT=""
        if "$REPO_DIR/scripts/install-launch-agent" > "${LAUNCH_AGENT_OUTPUT:-/dev/null}" 2>&1; then
          AUTO_START_ENABLED=true
          echo "  ✓ 已开启：重启后可以直接继续使用"
        else
          echo "  ✗ 自动开启失败；本次仍可手动运行 bash start.sh。"
          if [ -n "$LAUNCH_AGENT_OUTPUT" ] && [ -s "$LAUNCH_AGENT_OUTPUT" ]; then
            sed -n '1,8p' "$LAUNCH_AGENT_OUTPUT"
          fi
        fi
        [ -n "$LAUNCH_AGENT_OUTPUT" ] && rm -f "$LAUNCH_AGENT_OUTPUT"
        ;;
      *)
        echo "  ○ 已跳过。之后如果重启 Mac，需要手动运行：bash start.sh"
        ;;
    esac
  else
    echo "  ○ 当前系统不支持自动配置；重启后请手动运行：bash start.sh"
  fi
fi

if [ "$SETUP_PREVIEW_MODE" != true ] && [ "$AUTO_START_ENABLED" != true ]; then
  echo ""
  echo "→ 启动 Margin..."
  mkdir -p "$DATA_DIR/.logs"
  START_OUTPUT="$DATA_DIR/.logs/setup-start.log"
  if bash "$REPO_DIR/start.sh" > "$START_OUTPUT" 2>&1 && grep -q "工作台已就绪" "$START_OUTPUT"; then
    STARTED_NOW=true
    echo "  ✓ Margin 已启动"
  else
    echo "  ✗ 自动启动失败。"
    echo "  请运行 bash start.sh 查看原因，或查看日志：$START_OUTPUT"
  fi
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "${GREEN}安装完成！${RESET}"
echo ""
if [ "$SETUP_PREVIEW_MODE" != true ] && { [ "$AUTO_START_ENABLED" = true ] || [ "$STARTED_NOW" = true ]; }; then
  echo "✓ Margin 已自动启动后端服务。"
  echo ""
elif [ "$SETUP_PREVIEW_MODE" != true ]; then
  echo "如果刚才没有启动成功，请运行："
  echo ""
  echo "  bash start.sh"
  echo ""
fi
echo "开始使用前，请在 Chrome 加载插件（开发者模式 → 加载已解压的扩展程序 → 选择本仓库根目录）"
echo "═══════════════════════════════════════════════════════"
