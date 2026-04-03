/**
 * 评论区自动化测试
 *
 * 测试流程：
 * 1. 启动 Chrome（加载插件）
 * 2. 打开 localhost:8765（知识库本地服务）
 * 3. 选中一段文字，触发右键菜单"评论"
 * 4. 断言：高亮出现 + 评论面板出现
 * 5. 输入评论，点发送
 * 6. 断言：评论卡片出现 + localStorage 有记录
 * 7. 刷新页面，断言：历史评论 badge 出现（持久化）
 * 8. 点 badge 打开面板，断言：历史评论仍在
 * 9. 点"召唤 AI"，断言：AI 回复气泡出现（真实 OpenRouter 调用）
 *
 * 运行：node scripts/test-comment-system.js
 */

const { chromium } = require('playwright');
const path = require('path');

const EXTENSION_PATH = path.resolve(__dirname, '..');
const TEST_URL = 'http://localhost:8765';
const PASS = (msg) => console.log(`  ✅ ${msg}`);
const FAIL = (msg) => { console.error(`  ❌ ${msg}`); process.exit(1); };
const LOG = (msg) => console.log(`\n→ ${msg}`);

async function run() {
  LOG('启动 Chrome（加载插件）');
  const ctx = await chromium.launchPersistentContext('', {
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
    ],
    viewport: { width: 1280, height: 800 },
  });

  const page = await ctx.newPage();

  // ── 1. 打开知识库 ──────────────────────────────────────
  LOG('打开知识库首页');
  await page.goto(TEST_URL, { waitUntil: 'networkidle' });

  // 找一篇文章链接，点进去
  const firstLink = page.locator('.sidebar a').nth(1);
  const href = await firstLink.getAttribute('href').catch(() => null);
  if (!href) {
    // 如果没有文章，直接用首页测试
    LOG('（没有找到文章链接，用首页测试）');
  } else {
    await firstLink.click();
    await page.waitForLoadState('networkidle');
  }

  // ── 2. 清空 localStorage（确保干净状态）────────────────
  await page.evaluate(() => {
    Object.keys(localStorage).filter(k => k.startsWith('kb_comments_')).forEach(k => localStorage.removeItem(k));
  });
  PASS('localStorage 清空');

  // ── 3. 选中文字，触发右键菜单 ──────────────────────────
  LOG('选中文字，触发右键"评论"');

  // 找到第一段文字内容
  const textEl = page.locator('p, li, td').first();
  await textEl.waitFor({ timeout: 5000 }).catch(() => {});

  // 用 JS 模拟选中文字（右键菜单在 Playwright 里通过 dispatchEvent 模拟，
  // 实际选中后发送 ADD_COMMENT 消息更可靠）
  const excerpt = await page.evaluate(() => {
    const el = document.querySelector('p, li, td, h2');
    if (!el || !el.textContent.trim()) return null;
    const text = el.textContent.trim().slice(0, 50);
    // 模拟 window.getSelection
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return text;
  });

  if (!excerpt) FAIL('找不到可选中的文字');
  PASS(`模拟选中文字：「${excerpt.slice(0,30)}...」`);

  // 直接用 chrome.runtime.sendMessage 模拟右键菜单触发（绕过真实右键）
  // 在 page context 里通过 chrome extension API 不可达，改为直接调用 commentSystem.open
  // 通过 postMessage 桥触发 content script 的 commentSystem.open
  await page.evaluate((text) => {
    window.postMessage({ __kb_test: 'open_comment', excerpt: text, url: location.href, title: document.title }, '*');
  }, excerpt);

  await page.waitForTimeout(500);

  const panelExists = await page.locator('#kb-comment-panel').count();
  if (!panelExists) FAIL('评论面板未出现');
  PASS('评论面板已出现');

  // ── 4. 检查 quote 预览 ─────────────────────────────────
  const quotePreview = await page.locator('#kb-cp-quote-preview').isVisible().catch(() => false);
  if (quotePreview) PASS('划线内容预览显示正常');

  // ── 5. 输入评论，发送 ──────────────────────────────────
  LOG('输入评论并发送');
  await page.locator('#kb-cp-textarea').fill('这是自动化测试评论 ' + Date.now());
  await page.locator('#kb-cp-send-btn').click();
  await page.waitForTimeout(300);
  PASS('评论发送完成');

  // ── 6. 验证 localStorage ───────────────────────────────
  const stored = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(k => k.startsWith('kb_comments_'));
    if (!key) return null;
    return JSON.parse(localStorage.getItem(key));
  });
  if (!stored || stored.length === 0) FAIL('localStorage 没有评论记录');
  PASS(`localStorage 有 ${stored.length} 条评论记录`);

  // ── 7. 刷新页面，验证持久化 ────────────────────────────
  LOG('刷新页面，验证历史评论');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  const badge = await page.locator('#kb-comment-badge').isVisible().catch(() => false);
  if (!badge) FAIL('刷新后没有历史评论 badge');
  PASS('刷新后历史评论 badge 出现');

  // ── 8. 打开面板，检查历史 ──────────────────────────────
  await page.locator('#kb-comment-badge').click();
  await page.waitForTimeout(300);
  const cards = await page.locator('.kb-cmt-card').count();
  if (cards === 0) FAIL('历史评论卡片未渲染');
  PASS(`历史评论卡片 ${cards} 条`);

  // ── 9. 召唤 AI 回复 ────────────────────────────────────
  LOG('点击召唤 AI 回复（真实 OpenRouter 调用，最多等60秒）');

  // 清除上次的 AI 回复标记
  await page.evaluate(() => localStorage.removeItem('__kb_last_ai_reply'));

  const aiBtn = page.locator('.kb-ai-btn').first();
  await aiBtn.click();

  // 轮询 localStorage 等待 AI 回复（localStorage 跨 world 共享，content script 和 main world 都能读写）
  const start = Date.now();
  let aiResult = null;
  while (Date.now() - start < 60000) {
    aiResult = await page.evaluate(() => {
      const v = localStorage.getItem('__kb_last_ai_reply');
      return v ? JSON.parse(v) : null;
    });
    if (aiResult) break;
    await page.waitForTimeout(1000);
  }

  if (!aiResult) {
    FAIL('AI 回复超时（60秒内 localStorage 无结果）');
  } else if (!aiResult.ok) {
    FAIL(`AI 调用失败：${aiResult.reply}`);
  } else {
    PASS(`AI 回复成功：${aiResult.reply.slice(0, 80)}`);
  }

  // ── 10. 验证 Notion 回写 ──────────────────────────────
  LOG('验证 Notion 回写（等5秒让 background 完成写入）');
  await page.waitForTimeout(5000);

  // 查 Notion 数据库最新一条记录
  const https = require('https');
  const notionResult = await new Promise((resolve) => {
    const body = JSON.stringify({ page_size: 1, sorts: [{ timestamp: 'created_time', direction: 'descending' }] });
    const req = https.request({
      hostname: 'api.notion.com',
      path: '/v1/databases/DATABASE_ID_REMOVED/query',
      method: 'POST',
      headers: {
        'Authorization': 'Bearer NOTION_TOKEN_REMOVED',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body)
      }
    }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch(e) { console.log('  Notion parse error:', data.slice(0,200)); resolve(null); }
      });
    });
    req.on('error', (e) => { console.log('  Notion request error:', e.message); resolve(null); });
    req.write(body);
    req.end();
  });

  if (notionResult && notionResult.results && notionResult.results.length > 0) {
    const latest = notionResult.results[0];
    const title = latest.properties?.标题?.title?.[0]?.text?.content || '';
    const createdAt = latest.created_time;
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    if (createdAt > fiveMinAgo && title.includes('[评论]')) {
      PASS(`Notion 回写成功：「${title}」`);
    } else {
      console.log(`  最新 Notion 记录：${title} (${createdAt})`);
      FAIL('Notion 最新记录不是刚写入的评论');
    }
  } else {
    FAIL('无法查询 Notion 数据库');
  }

  // ── 11. 验证收起按钮 ──────────────────────────────────
  LOG('验证收起按钮');
  await page.locator('#kb-cp-close').click();
  await page.waitForTimeout(300);
  const hidden = await page.locator('#kb-comment-panel.kb-hidden').count();
  if (!hidden) FAIL('收起按钮无效，面板未隐藏');
  PASS('收起按钮正常');

  LOG('所有测试通过 🎉');
  await ctx.close();
}

run().catch(err => {
  console.error('\n测试失败:', err);
  process.exit(1);
});
