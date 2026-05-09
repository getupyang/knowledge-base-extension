#!/bin/bash
# ═══════════════════════════════════════════════════════
# 知识库助手 — 首次安装脚本
# 用法：bash setup.sh
# ═══════════════════════════════════════════════════════

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
CONFIG_FILE="$HOME/.kb_config"

echo "=== 知识库助手 首次安装 ==="
echo ""

# ── 1. 检查依赖 ──────────────────────────────────────────
echo "→ 检查依赖..."

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
check_cmd node "请安装 Node.js：https://nodejs.org" || DEPS_OK=false

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
  echo "  ○ claude 未找到（可选：安装 Claude Code 后可走本地订阅额度）"
else
  echo "  ✓ claude optional ($CLAUDE_BIN)"
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
  echo "  ○ codex 未找到（可选：安装 Codex CLI 后可走本地订阅额度）"
else
  echo "  ✓ codex optional ($CODEX_BIN)"
fi

if [ "$DEPS_OK" = false ]; then
  echo ""
  echo "请安装以上依赖后重新运行此脚本。"
  exit 1
fi

choose_qwen_endpoint_for_setup() {
  echo ""
  echo "  先选千问 API 类型。"
  echo ""
  echo "  1) 阿里云百炼 / 中国内地标准 API  普通百炼 API Key 通常选这个"
  echo "  2) Qwen Global / 新加坡标准 API   海外或 Global Key 选这个"
  echo "  3) Qwen Coding Plan / 中国内地    只有 Coding Plan 订阅用户选"
  echo "  4) Qwen Coding Plan / Global      只有 Coding Plan 订阅用户选"
  echo "  5) 手动填写 Base URL"
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
      API_PROVIDER_LABEL="千问 / Qwen API（自定义 endpoint）"
      API_BASE_URL_DEFAULT=""
      ;;
    *)
      echo "  ✗ 没看懂这个选择：$QWEN_ENDPOINT_CHOICE"
      exit 1
      ;;
  esac
}

choose_qwen_model_for_setup() {
  echo ""
  echo "  再选默认模型。"
  echo ""
  echo "  1) qwen3.5-plus  推荐默认，OpenClaw Qwen provider 默认模型"
  echo "  2) qwen3.6-plus  更强；标准 API 更适合，Coding Plan 可能不支持"
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
      read_with_default_for_setup "Model" "" LLM_MODEL
      ;;
    *)
      echo "  ✗ 没看懂这个模型选择：$QWEN_MODEL_CHOICE"
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
      echo "  ✗ 没看懂这个模型选择：$OPENROUTER_MODEL_CHOICE"
      exit 1
      ;;
  esac
}

choose_api_preset() {
  echo "  请选择 API 服务商："
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
      echo "  ✗ 没看懂这个选择：$API_CHOICE"
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
  echo "  请选择 Claude Code 的连接方式："
  echo ""
  echo "  1) Claude 账号登录 / Claude Code 本机配置  推荐；不需要输入 API Key"
  echo "  2) Anthropic API Key 模式                 只有你平时用 ANTHROPIC_API_KEY 跑 claude 时选"
  echo ""
  read -p "  输入 1 / 2 后回车 [1]：" CLAUDE_AUTH_CHOICE
  CLAUDE_AUTH_CHOICE="${CLAUDE_AUTH_CHOICE:-1}"

  case "$CLAUDE_AUTH_CHOICE" in
    1)
      echo "  正在用账号登录模式测试 Claude Code..."
      if ! run_claude_probe_for_setup account; then
        print_claude_probe_failure_for_setup
        echo "  ✗ 账号登录模式不可用，本次没有完成模型配置。"
        echo "  请先确认这个命令在终端里能跑通，然后重新运行 setup："
        echo "    claude -p \"Reply with exactly OK.\" --output-format json"
        exit 1
      fi
      ANTHROPIC_API_KEY_FOR_CONFIG=""
      echo "  ✓ Claude Code 账号登录/本机配置可用，不需要保存 API Key。"
      ;;
    2)
      if [ -n "$ANTHROPIC_API_KEY_FOR_CONFIG" ]; then
        echo "  已检测到当前终端的 ANTHROPIC_API_KEY（长度 ${#ANTHROPIC_API_KEY_FOR_CONFIG} 位，已隐藏）。"
      else
        echo "  请粘贴 ANTHROPIC_API_KEY。为安全起见，粘贴时屏幕不会显示字符。"
        read_optional_secret_for_setup "ANTHROPIC_API_KEY：" ANTHROPIC_API_KEY_FOR_CONFIG
      fi
      if [ -z "$ANTHROPIC_API_KEY_FOR_CONFIG" ]; then
        echo "  ✗ 没有收到 ANTHROPIC_API_KEY，本次没有完成模型配置。"
        exit 1
      fi
      ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_FOR_CONFIG"
      export ANTHROPIC_API_KEY
      echo "  正在用 API Key 模式测试 Claude Code..."
      if ! run_claude_probe_for_setup api_key; then
        print_claude_probe_failure_for_setup
        echo "  ✗ API Key 模式不可用，本次没有保存 API Key。"
        exit 1
      fi
      echo "  ✓ Claude Code API Key 模式可用，会保存 ANTHROPIC_API_KEY（长度 ${#ANTHROPIC_API_KEY_FOR_CONFIG} 位，已隐藏）。"
      ;;
    *)
      echo "  ✗ 没看懂这个选择：$CLAUDE_AUTH_CHOICE"
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
  echo "  Claude Code 非交互测试没有通过。"
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
  read_with_default_for_setup "LLM Base URL" "$API_BASE_URL_DEFAULT" LLM_BASE_URL
  LLM_BASE_URL="${LLM_BASE_URL%/}"
  if [ "$LLM_API_PROVIDER" = "qwen" ]; then
    choose_qwen_model_for_setup
  else
    choose_openrouter_model_for_setup
  fi
  echo "  ✓ API 标准模式：$API_PROVIDER_LABEL / $LLM_MODEL"
}

# ── 2. 安装 Python 依赖 ───────────────────────────────────
echo ""
echo "→ 安装 Python 依赖..."
pip3 install -q -r "$REPO_DIR/requirements.txt" && echo "  ✓ fastapi uvicorn pydantic"

# ── 3. 配置密钥 ───────────────────────────────────────────
echo ""
echo "→ 配置本地存储和可选 Notion 备份..."
echo ""
echo "  默认使用本地 SQLite：$DATA_DIR/comments.db"
echo "  不配置 Notion 也可以完整使用高亮、评论、AI 回复和记忆笔记本。"
echo "  如果你想额外同步一份云端备份，再填写 Notion Token 和 Database ID。"
echo "  详细图文教程：https://github.com/getupyang/knowledge-base-extension#optional-notion-backup"
echo ""
echo "  可选 Notion 备份步骤："
echo "  1. 打开 https://www.notion.so/my-integrations，创建 Integration，复制 Token"
echo "  2. 在 Notion 新建页面 → 选 Database - Full page"
echo "  3. 添加字段（名称必须一致）："
echo "       标题(title)  来源平台(select)  来源URL(url)"
echo "       原文片段(rich_text)  我的想法(rich_text)  评论区对话(rich_text)"
echo "  4. ⚠️ 关键一步：打开数据库页面 → 右上角 ··· → Connections → 选你的 Integration"
echo "     （不做这一步，插件写入 Notion 会报 'Could not find database' 错误）"
echo "  5. 数据库 URL 中 notion.so/xxxxxxxx?v=... 里 ?v= 之前的 xxxxxxxx 就是 Database ID"
echo "     （注意：?v= 后面的是 view ID，不要复制错）"
echo ""

if [ -f "$CONFIG_FILE" ]; then
  echo "  已有配置文件 $CONFIG_FILE"
  read -p "  是否重新配置？[y/N] " RECONFIG
  if [ "$RECONFIG" != "y" ] && [ "$RECONFIG" != "Y" ]; then
    echo "  跳过配置步骤。"
  else
    rm "$CONFIG_FILE"
  fi
fi

if [ ! -f "$CONFIG_FILE" ]; then
  read -p "  可选 Notion Token (ntn_...，留空跳过): " NOTION_TOKEN
  read -p "  可选 Notion Database ID (32位，留空跳过): " DATABASE_ID
  if { [ -n "$NOTION_TOKEN" ] && [ -z "$DATABASE_ID" ]; } || { [ -z "$NOTION_TOKEN" ] && [ -n "$DATABASE_ID" ]; }; then
    echo "  ✗ Notion 备份需要同时填写 Token 和 Database ID；都留空则关闭 Notion 备份。"
    exit 1
  fi

  echo ""
  echo "→ 配置 LLM..."
  echo "  先支持 4 种模型服务：Claude Code、Codex、千问 / Qwen API、OpenRouter API。"
  echo ""
  if [ -z "$CLAUDE_BIN" ] && [ -z "$CODEX_BIN" ]; then
    echo "  未检测到本地 Claude Code / Codex，请配置 API 标准模式。"
    configure_api_settings
  else
    LLM_API_PROVIDER=""
    LLM_API_KEY=""
    LLM_BASE_URL=""
    LLM_MODEL=""
    echo "  已检测到本地 agent。API 标准模式可选；不配置时本地 agent 失败不会自动走 API。"
    read -p "  是否现在配置 API 标准模式？[y/N] " CONFIGURE_API
    if [ "$CONFIGURE_API" = "y" ] || [ "$CONFIGURE_API" = "Y" ]; then
      configure_api_settings
    else
      echo "  跳过 API 标准模式。之后可运行 sh choose_ai_service.sh 切换。"
    fi
  fi

  if [ -z "$LLM_API_KEY" ] && [ -z "$CLAUDE_BIN" ] && [ -z "$CODEX_BIN" ]; then
    echo "  ✗ 未配置可用 LLM 后端。请填写 API key，或先安装 Claude Code / Codex CLI。"
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
    echo "  检测到多个可用模型服务，请选择默认使用哪一个。之后如需切换，运行 sh choose_ai_service.sh。"
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
# 知识库助手配置文件
# 修改后需要重新运行 start.sh

NOTION_TOKEN=${NOTION_TOKEN}
NOTION_DATABASE_ID=${DATABASE_ID}
MEMAI_LOCAL_BACKUP_ENABLED=1
# Recovery snapshots only. Primary data stays in comments.db and is not pruned by this count.
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
  echo "  ✓ 配置已写入 $CONFIG_FILE"
fi

# ── 4. 初始化数据库 ──────────────────────────────────────
echo ""
echo "→ 初始化本地数据库..."
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
print('  ✓ 数据库就绪:', db)
"

# ── 5. 初始化用户私有上下文 ─────────────────────────
if [ ! -f "$DATA_DIR/project_context.md" ]; then
  cat > "$DATA_DIR/project_context.md" << 'EOF'
# 项目上下文

（空白，用户还没有填写项目背景。AI 不能假设用户正在做某个项目。）

EOF
  echo "→ 已生成空的 $DATA_DIR/project_context.md（可选：填入你自己的项目背景）"
fi

if [ ! -f "$DATA_DIR/user_profile.md" ]; then
  cat > "$DATA_DIR/user_profile.md" << 'EOF'
# 用户画像

（空白，系统会根据这台电脑上的本地批注逐步学习。）

EOF
  echo "→ 已生成空的 $DATA_DIR/user_profile.md"
fi

if [ ! -f "$DATA_DIR/learned_rules.json" ]; then
  printf '{\n  "rules": []\n}\n' > "$DATA_DIR/learned_rules.json"
  echo "→ 已生成空的 $DATA_DIR/learned_rules.json"
fi

# ── 6. 验证可选 Notion 备份 ──────────────────────────────────
echo ""
echo "→ 验证可选 Notion 备份..."
source "$CONFIG_FILE" 2>/dev/null || true

if [ -n "$NOTION_TOKEN" ] && [ "$NOTION_TOKEN" != "ntn_your_token_here" ]; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $NOTION_TOKEN" \
    -H "Notion-Version: 2022-06-28" \
    "https://api.notion.com/v1/users/me" 2>/dev/null)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✓ Notion Token 有效"
  else
    echo "  ✗ Notion Token 无效（HTTP ${HTTP_CODE}），请检查 $CONFIG_FILE"
  fi
else
  echo "  ✓ 未开启 Notion 备份；数据会保存到本地 SQLite，并使用本地备份目录"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "安装完成！运行以下命令启动工作台："
echo ""
echo "  bash start.sh"
echo ""
echo "然后在 Chrome 加载插件（开发者模式 → 加载已解压的扩展程序 → 选择本仓库根目录）"
echo "访问知识库：http://localhost:8765"
echo "访问记忆笔记本：http://localhost:8765/notebook/"
echo "═══════════════════════════════════════════════════════"
