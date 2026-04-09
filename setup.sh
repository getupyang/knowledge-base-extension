#!/bin/bash
# ═══════════════════════════════════════════════════════
# 知识库助手 — 首次安装脚本
# 用法：bash setup.sh
# ═══════════════════════════════════════════════════════

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
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
  "$(which claude 2>/dev/null)"; do
  if [ -f "$p" ] && [ -x "$p" ]; then
    CLAUDE_BIN="$p"
    break
  fi
done

if [ -z "$CLAUDE_BIN" ]; then
  echo "  ✗ 缺少：claude（请先安装 Claude Code：npm install -g @anthropic-ai/claude-code）"
  DEPS_OK=false
else
  echo "  ✓ claude ($CLAUDE_BIN)"
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
echo "  2. 在 Notion 创建数据库，点击右上角 ··· → Add connections → 选你的 Integration"
echo "  3. 数据库 URL 中 notion.so/xxxxxxxx 里的 xxxxxxxx 就是 Database ID"
echo "  4. 数据库需要以下字段（名称必须一致）："
echo "       标题(title)  来源平台(select)  来源URL(url)"
echo "       原文片段(rich_text)  我的想法(rich_text)  评论区对话(rich_text)"
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

  cat > "$CONFIG_FILE" << EOF
# 知识库助手配置文件
# 修改后需要重新运行 start.sh

NOTION_TOKEN=${NOTION_TOKEN}
NOTION_DATABASE_ID=${DATABASE_ID}
CLAUDE_BIN=${CLAUDE_BIN}
RESEARCH_DIR=${BACKEND_DIR}
EOF

  chmod 600 "$CONFIG_FILE"
  echo "  ✓ 配置已写入 $CONFIG_FILE"
fi

# ── 4. 初始化数据库 ──────────────────────────────────────
echo ""
echo "→ 初始化本地数据库..."
python3 -c "
import sqlite3, os
db = os.path.join('$BACKEND_DIR', 'comments.db')
conn = sqlite3.connect(db)
conn.execute('''CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
conn.commit()
conn.close()
print('  ✓ 数据库就绪:', db)
"

# ── 5. 初始化 project_context.md ─────────────────────────
if [ ! -f "$BACKEND_DIR/project_context.md" ]; then
  cp "$BACKEND_DIR/project_context.template.md" "$BACKEND_DIR/project_context.md"
  echo "→ 已生成 backend/project_context.md，请填入你的项目背景（可选，让 AI 更懂你）"
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
echo "═══════════════════════════════════════════════════════"
