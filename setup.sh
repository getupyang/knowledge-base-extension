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

# ── 2. 安装 Python 依赖 ───────────────────────────────────
echo ""
echo "→ 安装 Python 依赖..."
pip3 install -q -r "$REPO_DIR/requirements.txt" && echo "  ✓ fastapi uvicorn pydantic"

# ── 3. 配置密钥 ───────────────────────────────────────────
echo ""
echo "→ 配置 Notion..."
echo ""
echo "  需要两样东西：Notion Token 和 Database ID"
echo "  详细图文教程：https://github.com/getupyang/knowledge-base-extension#notion-setup"
echo ""
echo "  简要步骤："
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
  read -p "  Notion Token (ntn_...): " NOTION_TOKEN
  read -p "  Notion Database ID (32位): " DATABASE_ID

  echo ""
  echo "→ 配置 LLM..."
  echo "  mem-ai 会优先使用本地 Claude Code / Codex（如果已安装），否则使用 OpenAI-compatible API。"
  echo "  API 可接 OpenRouter / OpenAI / DeepSeek / Kimi 等兼容接口。"
  echo ""
  if [ -z "$CLAUDE_BIN" ] && [ -z "$CODEX_BIN" ]; then
    echo "  未检测到本地 Claude Code / Codex，请配置 API key。"
  else
    echo "  已检测到本地 agent。API key 可留空；留空时本地 agent 失败不会自动走 API。"
  fi
  read -s -p "  LLM API Key（可选，回车跳过）: " LLM_API_KEY
  echo ""
  read -p "  LLM Base URL [https://openrouter.ai/api/v1]: " LLM_BASE_URL
  LLM_BASE_URL="${LLM_BASE_URL:-https://openrouter.ai/api/v1}"
  read -p "  LLM Model [openai/gpt-4o-mini]: " LLM_MODEL
  LLM_MODEL="${LLM_MODEL:-openai/gpt-4o-mini}"

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
    echo "  检测到多个可用 LLM 后端，请固定选择一个。之后如需切换，手动修改 ~/.kb_config 后重启。"
    echo "  可选：${AVAILABLE_PROVIDERS[*]}"
    while true; do
      read -p "  固定使用哪个后端？[${AVAILABLE_PROVIDERS[*]}]: " LLM_PROVIDER
      VALID_PROVIDER=false
      for p in "${AVAILABLE_PROVIDERS[@]}"; do
        if [ "$LLM_PROVIDER" = "$p" ]; then
          VALID_PROVIDER=true
          break
        fi
      done
      if [ "$VALID_PROVIDER" = true ]; then
        break
      fi
      echo "  请输入上面列出的一个值。"
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

  cat > "$CONFIG_FILE" << EOF
# 知识库助手配置文件
# 修改后需要重新运行 start.sh

NOTION_TOKEN=${NOTION_TOKEN}
NOTION_DATABASE_ID=${DATABASE_ID}
MEMAI_LLM_PROVIDER=${LLM_PROVIDER}
MEMAI_LOCAL_AGENT=${LOCAL_AGENT}
MEMAI_LLM_FALLBACK=${LLM_FALLBACK}
MEMAI_LLM_API_KEY=${LLM_API_KEY}
MEMAI_LLM_BASE_URL=${LLM_BASE_URL}
MEMAI_LLM_MODEL=${LLM_MODEL}
CLAUDE_BIN=${CLAUDE_BIN}
MEMAI_CLAUDE_BIN=${CLAUDE_BIN}
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

# ── 6. 验证 Notion Token ──────────────────────────────────
echo ""
echo "→ 验证 Notion 配置..."
source "$CONFIG_FILE" 2>/dev/null || true

if [ -n "$NOTION_TOKEN" ] && [ "$NOTION_TOKEN" != "ntn_your_token_here" ]; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $NOTION_TOKEN" \
    -H "Notion-Version: 2022-06-28" \
    "https://api.notion.com/v1/users/me" 2>/dev/null)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✓ Notion Token 有效"
  else
    echo "  ✗ Notion Token 无效（HTTP $HTTP_CODE），请检查 $CONFIG_FILE"
  fi
else
  echo "  ⚠ 未配置 Notion Token，Notion 写入功能不可用"
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
