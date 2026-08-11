// 真人交互验收环境：带界面 Chromium + 隔离后端（18766），真实数据零接触。
// 与 popup-acceptance-shots.js（自动截图）互补：这个起一个真人能点的窗口，一直开着。
// 隔离原则同截图 harness：独立 profile / 独立端口 / 独立数据目录；
// 兜底 route 拦截一切对真实 8766 的请求。
// 后端不在这里起——由验收主持人按剧本时机手动起停（扮演"agent 装好了"的瞬间）。
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const os = require("os");

const WORKTREE = path.resolve(__dirname, "..");
const SCRATCH = path.join(os.tmpdir(), "margin-popup-live");
const PROFILE = path.join(SCRATCH, "chrome-profile");
const PORT = 18766;

(async () => {
  fs.rmSync(PROFILE, { recursive: true, force: true });
  fs.mkdirSync(PROFILE, { recursive: true });

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chromium",
    headless: false,
    viewport: null,
    args: [
      `--disable-extensions-except=${WORKTREE}`,
      `--load-extension=${WORKTREE}`,
      "--window-size=420,780",
    ],
  });

  let sw = null;
  for (let i = 0; i < 40; i++) {
    sw = ctx.serviceWorkers()[0];
    if (sw) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  if (!sw) {
    console.error("[live] 扩展 service worker 没起来");
    process.exit(1);
  }
  const extId = new URL(sw.url()).host;

  // 兜底隔离：这个浏览器里任何页面都禁止触达真实 8766 服务
  await ctx.route("**://localhost:8766/**", (r) => r.abort());
  await ctx.route("**://127.0.0.1:8766/**", (r) => r.abort());

  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto(`chrome-extension://${extId}/src/popup/index.html`);
  await page.evaluate(
    (api) => chrome.storage.local.clear().then(() => chrome.storage.local.set({ kb_api_base_override: api })),
    `http://localhost:${PORT}`
  );
  await page.reload();
  console.log(`[live] ready — 扩展 ${extId}，popup 已在窗口里打开，指向隔离端口 ${PORT}`);

  ctx.on("close", () => {
    console.log("[live] 浏览器已关闭，验收结束");
    process.exit(0);
  });
})();
