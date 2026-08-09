// 七状态 popup 验收截图 harness
// 隔离原则：后端实例用独立 HOME + KB_DATA_DIR + 端口 18766 + 云同步 disabled，
// 不碰用户真实 ~/.kb_config、~/.knowledge-base-extension、8766 服务。
const { chromium } = require("playwright");
const { spawn, execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const WORKTREE = path.resolve(__dirname, "..");
const SCRATCH = path.join(os.tmpdir(), "margin-popup-shots");
fs.mkdirSync(SCRATCH, { recursive: true });
const ISO_HOME = path.join(SCRATCH, "iso-home");
const ISO_DATA = path.join(SCRATCH, "iso-data");
const PROFILE = path.join(SCRATCH, "chrome-profile");
const OUT = path.join(os.homedir(), "Desktop", "margin-popup-验收截图-2026-08-09");
const PORT = 18766;
const API = `http://localhost:${PORT}`;

let backendProc = null;
let shotIdx = 0;

function log(msg) { console.log(`[harness] ${msg}`); }

function backendEnv(extra) {
  return {
    ...process.env,
    HOME: ISO_HOME,
    // HOME 隔离会让 Python 丢失用户级 site-packages（uvicorn 装在
    // ~/Library/Python/3.9），用 PYTHONUSERBASE 指回真实包路径
    PYTHONUSERBASE: path.join(os.homedir(), "Library/Python/3.9"),
    KB_DATA_DIR: ISO_DATA,
    MARGIN_CLOUD_ENDPOINT: "disabled",
    MARGIN_INGEST_TOKEN: "disabled",
    MEMAI_LLM_FALLBACK: "fail",
    ...extra,
  };
}

async function startBackend(extra, label) {
  backendProc = spawn(
    "/usr/bin/python3",
    ["-m", "uvicorn", "agent_api:app", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: path.join(WORKTREE, "backend"), env: backendEnv(extra), stdio: ["ignore", "pipe", "pipe"] }
  );
  backendProc.stderr.on("data", (d) => fs.appendFileSync(path.join(SCRATCH, "backend.log"), d));
  backendProc.stdout.on("data", (d) => fs.appendFileSync(path.join(SCRATCH, "backend.log"), d));
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`${API}/health`);
      if (res.ok) { log(`backend up (${label})`); return; }
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`backend failed to start (${label})`);
}

async function stopBackend() {
  if (!backendProc) return;
  backendProc.kill("SIGTERM");
  await new Promise((r) => setTimeout(r, 800));
  try { backendProc.kill("SIGKILL"); } catch {}
  backendProc = null;
  log("backend stopped");
}

async function shot(page, name) {
  shotIdx += 1;
  const file = path.join(OUT, `${String(shotIdx).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  log(`shot ${path.basename(file)} (state=${await page.evaluate(() => document.body.dataset.state)})`);
}

async function setStorage(page, obj) {
  await page.evaluate((o) => chrome.storage.local.set(o), obj);
}

async function clearStorage(page) {
  await page.evaluate(() => chrome.storage.local.clear());
}

async function waitState(page, s, timeout = 15000) {
  try {
    await page.waitForFunction((st) => document.body.dataset.state === st, s, { timeout });
  } catch (e) {
    const now = await page.evaluate(() => document.body.dataset.state).catch(() => "?");
    throw new Error(`waitState(${s}) 超时，当前 state=${now}`);
  }
}

(async () => {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.rmSync(PROFILE, { recursive: true, force: true });
  fs.rmSync(ISO_HOME, { recursive: true, force: true });
  fs.rmSync(ISO_DATA, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(ISO_HOME, { recursive: true });
  fs.mkdirSync(ISO_DATA, { recursive: true });

  async function launchWithExtension() {
    const ctx = await chromium.launchPersistentContext(PROFILE, {
      channel: "chromium",
      headless: true,
      args: [
        `--disable-extensions-except=${WORKTREE}`,
        `--load-extension=${WORKTREE}`,
      ],
      viewport: { width: 380, height: 700 },
    });
    // MV3 service worker 惰性启动：轮询等待
    for (let i = 0; i < 40; i++) {
      const sw = ctx.serviceWorkers()[0];
      if (sw) return { ctx, extId: new URL(sw.url()).host };
      await new Promise((r) => setTimeout(r, 500));
    }
    await ctx.close();
    return null;
  }

  let launched = await launchWithExtension();
  if (!launched) {
    log("service worker 未出现，重启浏览器再试一次");
    fs.rmSync(PROFILE, { recursive: true, force: true });
    launched = await launchWithExtension();
  }
  if (!launched) throw new Error("扩展 service worker 两次都没起来");
  const context = launched.ctx;
  const extId = launched.extId;
  log(`extension id: ${extId}`);
  const POPUP = `chrome-extension://${extId}/src/popup/index.html`;

  // 兜底隔离：整个测试期间禁止触达用户真实 8766 服务
  await context.route("**://localhost:8766/**", (r) => r.abort());
  await context.route("**://127.0.0.1:8766/**", (r) => r.abort());

  const page = await context.newPage();
  page.on("console", (m) => log(`console[${m.type()}] ${m.text()}`));
  page.on("pageerror", (e) => log(`pageerror: ${e.message}`));
  // headless 下剪贴板可能不可写：stub 掉 writeText（仅影响"复制"按钮反馈，其余全真实）
  await page.addInitScript(() => {
    try {
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText: async () => {} },
        configurable: true,
      });
    } catch {}
  });

  // 让 popup 指向隔离后端实例（kb_api_base_override 是测试专用开关）
  await page.goto(POPUP);
  await clearStorage(page);
  await setStorage(page, { kb_api_base_override: API });

  // ── 场景 1：全新用户，引擎不在 → A ──
  await page.reload();
  await waitState(page, "A");
  await shot(page, "A-初始-选一个AI");

  // ── 场景 2：选 Codex → B 换屏（整屏滑动，非灰按钮） ──
  await page.click("#aChooseCodex");
  await waitState(page, "B");
  await page.waitForTimeout(400);
  await shot(page, "B-等待连接-给agent的话");

  // ── 场景 3：复制 → 等待行出现 ──
  await page.click("#bCopyBtn");
  await page.waitForSelector("#bWait:not([hidden])");
  await shot(page, "B-已复制-等待自动绿灯");

  // ── 场景 4：硬承诺——popup 开着不动，引擎上线（codex 就绪）→ 自动变绿灯 E ──
  await startBackend({ MEMAI_LLM_PROVIDER: "codex_cli", MEMAI_LOCAL_AGENT: "codex_cli" }, "configured/codex");
  await waitState(page, "E", 30000);
  await shot(page, "E-自动变绿灯-硬承诺真实轮询");

  // ── 场景 5：E 总览（含记忆笔记本按钮 + 划线批注 + Notion 独立区块） ──
  await page.waitForTimeout(600);
  await shot(page, "E-已连接总览");

  // ── 场景 6：F 切换屏（当前项标记 + 可用性说明） ──
  await page.click("#eSwitchBtn");
  await waitState(page, "F");
  await page.waitForTimeout(400);
  await shot(page, "F-切换AI-当前Codex");

  // ── 场景 7：真实内部即时切到 Claude Code（不重装） ──
  await page.click("#fClaude");
  await waitState(page, "E", 20000);
  await page.waitForTimeout(300);
  await shot(page, "E-已即时切换到ClaudeCode");

  // ── 场景 8：暂停 → G ──
  await page.click("#ePauseLink");
  await waitState(page, "G");
  await page.waitForTimeout(400);
  await shot(page, "G-已暂停");

  // 恢复回 E，清掉暂停标记，准备下一场景
  await page.click("#gResumeBtn");
  await waitState(page, "E");

  // ── 场景 9：C API 表单（引擎在线但未配 AI 的实例） ──
  await stopBackend();
  await startBackend({ MEMAI_LLM_PROVIDER: "api", MEMAI_LLM_API_PROVIDER: "qwen" }, "unconfigured/api-no-key");
  await clearStorage(page);
  await setStorage(page, { kb_api_base_override: API });
  await page.reload();
  await waitState(page, "A", 15000);
  await shot(page, "A-引擎在线未配AI");
  await page.click("#aChooseApi");
  await waitState(page, "C");
  await page.waitForTimeout(400);
  await shot(page, "C-API表单-域名自动配");

  // ── 场景 10：无效 Key → 真实调用验证 → 红字不跳走 ──
  await page.fill("#cKey", "sk-invalid-key-for-acceptance-test");
  await page.click("#cSaveBtn");
  await page.waitForSelector("#cErr:not([hidden])", { timeout: 40000 });
  await shot(page, "C-Key无效-红字不跳走");
  const stillC = await page.evaluate(() => document.body.dataset.state);
  if (stillC !== "C") throw new Error("C 态验证失败后不应跳走");

  // ── 场景 11：D 还没连上（等待超时 + 引擎失联） ──
  await stopBackend();
  await clearStorage(page);
  await setStorage(page, {
    kb_api_base_override: API,
    margin_conn_v1: { chosen: "codex_cli", phase: "waiting", waitingSince: Date.now() - 11 * 60 * 1000 },
  });
  await page.reload();
  await waitState(page, "D");
  await page.waitForTimeout(400);
  await shot(page, "D-还没连上");

  // ── 场景 12：D 诊断展开（真实读取：引擎无响应） ──
  await page.click("#dDiagToggle");
  await page.waitForFunction(() => {
    const el = document.getElementById("dDiagContent");
    return el && el.textContent && el.textContent !== "正在读取…";
  }, { timeout: 10000 });
  await shot(page, "D-诊断展开");

  // ── 场景 13：D 重试 → B 修复变体 prompt ──
  await page.click("#dRetryBtn");
  await waitState(page, "B");
  await page.waitForTimeout(400);
  await shot(page, "B-修复变体prompt");

  await context.close();
  await stopBackend();
  log(`done: ${shotIdx} screenshots in ${OUT}`);
})().catch(async (e) => {
  console.error("[harness] FAILED:", e.message);
  await stopBackend();
  process.exit(1);
});
