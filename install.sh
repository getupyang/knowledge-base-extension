#!/bin/bash
# Margin 一条命令安装（macOS）
#
#   curl -fsSL https://raw.githubusercontent.com/getupyang/knowledge-base-extension/main/install.sh | bash
#
# 幂等：重复运行 = 更新代码 + 重启服务。
# 承诺：不触碰已有批注数据（~/.knowledge-base-extension 下的 db/记忆），
#       不修改用户的 claude / codex 安装与登录状态，只读取路径做探活。
# 自测：MARGIN_INSTALL_DRY_RUN=1 bash install.sh  （只做检测与计划输出，不写配置、不启动服务）

set -u

DRY_RUN="${MARGIN_INSTALL_DRY_RUN:-0}"
REPO_URL="https://github.com/getupyang/knowledge-base-extension.git"
DATA_DIR="${KB_DATA_DIR:-$HOME/.knowledge-base-extension}"
APP_DIR="${MARGIN_APP_DIR:-$DATA_DIR/app}"
CONFIG_FILE="$HOME/.kb_config"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
die()  { printf '  ✗ %s\n' "$*" >&2; exit 1; }

say "=== Margin 安装 ==="

# ── 平台与基础依赖 ────────────────────────────────────────
case "$(uname -s)" in
  Darwin) ;;
  MINGW*|MSYS*|CYGWIN*)
    die "Windows 请按仓库 WINDOWS.md 的步骤安装（PowerShell 运行 setup.ps1）。" ;;
  *)
    die "目前一条命令安装只支持 macOS；Linux 支持在计划中。" ;;
esac

command -v git >/dev/null 2>&1 || die "缺少 git。请先运行：xcode-select --install"
command -v python3 >/dev/null 2>&1 || die "缺少 python3。请先运行：xcode-select --install"
ok "git / python3 可用"

# ── 获取或更新代码 ────────────────────────────────────────
# 若从本地仓库内运行（开发者/已 clone 用户），就地使用；否则 clone 到数据目录下的 app/
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [ -n "$SCRIPT_SRC" ] && [ -f "$(dirname "$SCRIPT_SRC")/manifest.json" ]; then
  APP_DIR="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  ok "使用本地仓库：$APP_DIR"
elif [ -d "$APP_DIR/.git" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    ok "dry-run：将更新 ${APP_DIR} （git pull --ff-only）"
  else
    say "→ 更新代码：$APP_DIR"
    git -C "$APP_DIR" pull --ff-only || warn "代码更新失败，继续使用当前版本"
  fi
else
  if [ "$DRY_RUN" = "1" ]; then
    ok "dry-run：将 clone $REPO_URL 到 $APP_DIR"
  else
    say "→ 下载代码到 $APP_DIR"
    mkdir -p "$(dirname "$APP_DIR")"
    git clone --depth 1 "$REPO_URL" "$APP_DIR" || die "下载失败，请检查网络后重试"
  fi
fi

# ── Python 依赖 ───────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  ok "dry-run：将安装 Python 依赖（fastapi/uvicorn/pydantic/markdown）"
elif [ -f "$APP_DIR/requirements.txt" ]; then
  say "→ 安装 Python 依赖..."
  python3 -m pip install -q -r "$APP_DIR/requirements.txt" 2>/dev/null \
    || python3 -m pip install -q --user -r "$APP_DIR/requirements.txt" \
    || die "Python 依赖安装失败。请手动运行：python3 -m pip install -r $APP_DIR/requirements.txt"
  ok "Python 依赖就绪"
fi

# ── AI 供能发现（探活验证，不信任任何固定路径）──────────────
# 原则：候选必须实际跑通 --version 才算可用；"文件存在"不等于"可用"。
probe_bin() {
  [ -n "${1:-}" ] && [ -x "$1" ] && "$1" --version >/dev/null 2>&1
}

discover_bin() {
  # $1 = 命令名（claude / codex）。输出第一个探活通过的绝对路径。
  name="$1"
  from_shell=""
  if [ -n "${SHELL:-}" ]; then
    from_shell="$("$SHELL" -l -c "command -v $name" 2>/dev/null | tail -1)"
  fi
  # ChatGPT 桌面版内嵌完整的 codex CLI（与 App 共用登录态），很多用户只有它、没有独立 CLI
  app_bundled=""
  if [ "$name" = "codex" ]; then
    app_bundled="/Applications/ChatGPT.app/Contents/Resources/codex"
  fi
  for cand in \
    "$from_shell" \
    "$HOME/.npm-global/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name" \
    "$HOME/.local/bin/$name" \
    "$HOME/.volta/bin/$name" \
    "$HOME/.bun/bin/$name" \
    "$app_bundled"; do
    if probe_bin "$cand"; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

say "→ 检测本机 AI（Claude Code / Codex）..."
CLAUDE_FOUND="$(discover_bin claude || true)"
CODEX_FOUND="$(discover_bin codex || true)"
[ -n "$CLAUDE_FOUND" ] && ok "Claude Code：$CLAUDE_FOUND" || warn "未检测到可用的 Claude Code"
[ -n "$CODEX_FOUND" ] && ok "Codex：$CODEX_FOUND" || warn "未检测到可用的 Codex CLI"
if [ -z "$CLAUDE_FOUND" ] && [ -z "$CODEX_FOUND" ]; then
  warn "两者都没有也没关系：装完后在浏览器 Margin 图标里粘贴一个 API Key 即可使用 AI"
fi

# ── 写配置（只补缺与修坏，不覆盖用户已有的有效配置）──────────
upsert_config() {
  # $1=key $2=value：已有该 key 则原地替换，否则追加
  key="$1"; value="$2"
  if grep -q "^${key}=" "$CONFIG_FILE" 2>/dev/null; then
    sed -i '' "s|^${key}=.*|${key}=${value}|" "$CONFIG_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$CONFIG_FILE"
  fi
}

config_value() {
  grep "^${1}=" "$CONFIG_FILE" 2>/dev/null | tail -1 | cut -d= -f2-
}

if [ "$DRY_RUN" = "1" ]; then
  ok "dry-run：将按检测结果写 ${CONFIG_FILE} （只补缺失项，修复探活失败的路径）"
else
  if [ ! -f "$CONFIG_FILE" ]; then
    say "→ 生成配置 $CONFIG_FILE"
    {
      echo "# Margin 本地服务配置（install.sh 生成，可用浏览器里的 Margin 图标修改 AI 服务）"
      echo "KB_DATA_DIR=$DATA_DIR"
      echo "MEMAI_LLM_PROVIDER=auto"
      echo "MEMAI_LLM_FALLBACK=fail"
    } > "$CONFIG_FILE"
    if [ -n "$CLAUDE_FOUND" ]; then
      upsert_config MEMAI_LOCAL_AGENT claude_code
    elif [ -n "$CODEX_FOUND" ]; then
      upsert_config MEMAI_LOCAL_AGENT codex_cli
    fi
  fi
  # 无论新旧配置：补齐/修复探活验证过的二进制路径
  if [ -n "$CLAUDE_FOUND" ]; then
    existing="$(config_value MEMAI_CLAUDE_BIN)"
    if ! probe_bin "$existing"; then
      upsert_config MEMAI_CLAUDE_BIN "$CLAUDE_FOUND"
      upsert_config CLAUDE_BIN "$CLAUDE_FOUND"
    fi
  fi
  if [ -n "$CODEX_FOUND" ]; then
    existing="$(config_value MEMAI_CODEX_BIN)"
    if ! probe_bin "$existing"; then
      upsert_config MEMAI_CODEX_BIN "$CODEX_FOUND"
    fi
  fi
  ok "配置就绪：$CONFIG_FILE"
fi

# ── 启动服务 + 注册开机自启 ────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  ok "dry-run：将运行 start.sh 启动服务并注册开机自启（launchd）"
  say ""
  say "dry-run 完成，没有改动任何东西。"
  exit 0
fi

say "→ 启动 Margin 本地服务..."
bash "$APP_DIR/start.sh" || die "服务启动失败。日志：$DATA_DIR/.logs/"

say "→ 注册开机自动启动..."
"$APP_DIR/scripts/install-launch-agent" || warn "自启注册失败（不影响本次使用），可稍后重跑 install.sh"

say ""
say "=== 安装完成 ==="
say "回到 Chrome，点击工具栏的 Margin 图标——看到绿色状态即可开始使用。"
say "（还没装插件？Chrome 商店搜索 Margin，或按仓库 README 加载。）"
say "数据都在本机：$DATA_DIR"
