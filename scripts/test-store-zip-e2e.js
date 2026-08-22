#!/usr/bin/env node
/**
 * 5.3 + 5.9：商店 zip 真实解包 E2E（macOS）
 *
 * 流程：重新构建商店 zip → 解包到临时目录 → Chrome 以【解包后的商店包】加载插件
 *（不是仓库目录——测的就是用户从商店拿到的那份）→ 两幕验证：
 *   第一幕（引擎在线）：划线 → 评注 → 发送 → 面板出现评论卡（核心流程）
 *   第二幕（引擎真死，需外部先停 8766）：同流程仍可用 + 刷新后批注仍在（本地兜底）
 * 顺手把关键画面截成 1280x800 存 dist/store-assets/（5.7 商店截图候选）。
 *
 * 用法：
 *   node scripts/test-store-zip-e2e.js online    # 第一幕（8766 需在跑）
 *   node scripts/test-store-zip-e2e.js offline   # 第二幕（8766 需已停）
 */
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const MODE = process.argv[2] || 'online';
const SHOTS = path.join(ROOT, 'dist', 'store-assets');
const TEST_URL = 'http://localhost:8765';

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });

  // 1) 构建 + 解包商店 zip（永远测最新构建产物）
  execSync('python3 scripts/build-store-zip', { cwd: ROOT, stdio: 'pipe' });
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'manifest.json'), 'utf8'));
  const zipPath = path.join(ROOT, 'dist', `margin-v${manifest.version}.zip`);
  const unpacked = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-store-'));
  execSync(`unzip -q "${zipPath}" -d "${unpacked}"`);
  console.log(`✓ zip 已解包：${unpacked}`);

  // 2) 以解包商店包启动 Chrome
  const ctx = await chromium.launchPersistentContext('', {
    headless: false,
    viewport: { width: 1280, height: 800 },
    args: [
      `--disable-extensions-except=${unpacked}`,
      `--load-extension=${unpacked}`,
    ],
  });
  const page = await ctx.newPage();
  await page.goto(TEST_URL, { waitUntil: 'domcontentloaded' }).catch(() => {
    throw new Error('8765 阅读服务必须在跑（两幕都依赖它作为测试页面）');
  });
  await page.waitForTimeout(1500); // content script 注入 + init

  // 3) 划线 → 评注 → 发送（经受信来源桥，与真实用户点"评注"同一条代码路径入口）
  const excerpt = await page.evaluate(() => {
    const el = [...document.querySelectorAll('a,p,h3,li')].find(n => n.textContent.trim().length > 8);
    const text = el.textContent.trim().slice(0, 20);
    window.postMessage({ __kb_test: 'open_comment', excerpt: text, url: location.href, title: document.title }, '*');
    return text;
  });
  await page.waitForSelector('#kb-cp-textarea', { timeout: 5000 });
  const note = `商店包E2E-${MODE}-${manifest.version}`;
  await page.fill('#kb-cp-textarea', note);
  await page.click('#kb-cp-send-btn');
  await page.waitForTimeout(2500);

  // 4) 断言：评论卡出现 + 高亮已持久化（发送成功 = 临时高亮转正）
  const panelText = await page.evaluate(() => document.getElementById('kb-comment-panel')?.textContent || document.body.textContent);
  if (!panelText.includes(note)) throw new Error(`评论未出现在面板：${note}`);
  const persisted = await page.evaluate(() =>
    Object.keys(localStorage).some(k => localStorage.getItem(k)?.includes('商店包E2E')));
  if (!persisted) throw new Error('批注未持久化到本地存储');
  console.log(`✓ 划线→评注→发送 成功（${MODE}）：面板可见 + 本地已持久化`);
  await page.screenshot({ path: path.join(SHOTS, `e2e-${MODE}-comment.png`) });

  // 5) 刷新后批注仍在（无后端幕的关键断言：本地兜底不依赖引擎）
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const afterReload = await page.evaluate(() =>
    Object.keys(localStorage).some(k => localStorage.getItem(k)?.includes('商店包E2E')));
  if (!afterReload) throw new Error('刷新后批注丢失');
  console.log(`✓ 刷新后批注仍在（${MODE}）`);

  // 6) popup 截图（商店素材候选）：从 service worker 拿扩展 ID
  const sw = ctx.serviceWorkers()[0];
  if (sw) {
    const extId = new URL(sw.url()).host;
    const popup = await ctx.newPage();
    await popup.setViewportSize({ width: 420, height: 720 });
    await popup.goto(`chrome-extension://${extId}/src/popup/index.html`);
    await popup.waitForTimeout(2000);
    await popup.screenshot({ path: path.join(SHOTS, `e2e-${MODE}-popup.png`) });
    console.log(`✓ popup 截图（${MODE} 状态）已存`);
  }

  await ctx.close();
  fs.rmSync(unpacked, { recursive: true, force: true });
  console.log(`\n=== 5.${MODE === 'online' ? '3 商店包核心流程' : '9 无后端合规'} E2E 通过 ===`);
}

main().catch(err => { console.error(`✗ ${err.message}`); process.exit(1); });
