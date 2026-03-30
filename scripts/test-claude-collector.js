/**
 * 自动测试：Claude.ai 对话采集 → Notion 存储
 *
 * 运行方式：node scripts/test-claude-collector.js
 *
 * 测试流程：
 * 1. 启动Chrome，加载插件，复用已有登录态
 * 2. 打开claude.ai，发送一条测试消息
 * 3. 等待AI回复完成
 * 4. 等待采集器触发（4秒防抖）
 * 5. 查询Notion数据库，验证新记录是否出现
 * 6. 输出测试结果
 */

const { chromium } = require('playwright');
const https = require('https');

// ── 配置 ──────────────────────────────────────────────
const NOTION_TOKEN = "NOTION_TOKEN_HERE";
const DATABASE_ID = "32dae139524480ecbeb4fb76b0269245";
const EXTENSION_PATH = require('path').resolve(__dirname, '..');
const CHROME_PROFILE = `${process.env.HOME}/Library/Application Support/Google/Chrome/Default`;
const TEST_MESSAGE = `知识库插件自动测试 ${Date.now()}`;

// ── Notion查询 ─────────────────────────────────────────
function notionRequest(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = https.request({
      hostname: 'api.notion.com',
      path,
      method,
      headers: {
        'Authorization': `Bearer ${NOTION_TOKEN}`,
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {})
      }
    }, res => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => resolve(JSON.parse(buf)));
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function queryLatestNotionRecord() {
  const result = await notionRequest('POST', `/v1/databases/${DATABASE_ID}/query`, {
    sorts: [{ timestamp: 'created_time', direction: 'descending' }],
    page_size: 1
  });
  return result.results?.[0];
}

// ── 主测试逻辑 ─────────────────────────────────────────
async function runTest() {
  console.log('🧪 开始测试：Claude.ai 对话采集\n');
  const startTime = Date.now();

  // 记录测试前最新记录的时间，用于判断是否有新记录
  console.log('📋 查询当前Notion最新记录...');
  const beforeRecord = await queryLatestNotionRecord();
  const beforeTime = beforeRecord
    ? new Date(beforeRecord.created_time).getTime()
    : 0;
  console.log(`   测试前最新记录时间: ${beforeRecord ? new Date(beforeTime).toLocaleTimeString() : '无记录'}\n`);

  // 启动浏览器
  console.log('🌐 启动Chrome，加载插件...');
  let browser;
  try {
    browser = await chromium.launchPersistentContext(CHROME_PROFILE, {
      headless: false,
      args: [
        `--load-extension=${EXTENSION_PATH}`,
        `--disable-extensions-except=${EXTENSION_PATH}`,
        '--no-first-run',
        '--no-default-browser-check',
      ],
      viewport: { width: 1280, height: 800 }
    });
  } catch (e) {
    console.error('❌ Chrome启动失败（可能Chrome正在运行，请关闭后重试）:', e.message);
    process.exit(1);
  }

  const page = await browser.newPage();

  // 收集控制台日志
  const logs = [];
  page.on('console', msg => {
    const text = msg.text();
    if (text.includes('[知识库]')) {
      logs.push(text);
      console.log(`   浏览器: ${text}`);
    }
  });

  try {
    // 打开claude.ai
    console.log('📂 打开 claude.ai...');
    await page.goto('https://claude.ai/new', { waitUntil: 'domcontentloaded', timeout: 30000 });

    // 等待登录态加载，最多15秒
    console.log('⏳ 等待页面加载（最多15秒）...');
    try {
      await page.waitForSelector('[data-testid="chat-input"]', { timeout: 15000 });
    } catch (e) {
      // 截图帮助调试
      await page.screenshot({ path: '/tmp/claude-test-debug.png' });
      console.error('❌ 未检测到输入框，截图已保存到 /tmp/claude-test-debug.png');
      console.error('   当前URL:', page.url());
      console.error('   页面标题:', await page.title());
      await browser.close();
      process.exit(1);
    }
    console.log('✅ 已登录，检测到输入框\n');

    // 发送测试消息
    console.log(`💬 发送测试消息: "${TEST_MESSAGE}"`);
    await page.click('[data-testid="chat-input"]');
    await page.type('[data-testid="chat-input"]', TEST_MESSAGE, { delay: 50 });
    await page.keyboard.press('Enter');

    // 等待AI开始回复
    console.log('⏳ 等待AI回复...');
    await page.waitForSelector('.font-claude-response', { timeout: 30000 });

    // 等待AI回复完成（复制按钮出现）
    console.log('⏳ 等待AI回复完成...');
    await page.waitForSelector('[data-testid="action-bar-copy"]', { timeout: 60000 });
    console.log('✅ AI回复完成\n');

    // 等待采集器触发（4秒防抖 + 1秒余量）
    console.log('⏳ 等待采集器触发（5秒）...');
    await page.waitForTimeout(5000);

    // 验证Notion是否有新记录
    console.log('📋 查询Notion数据库...');
    const afterRecord = await queryLatestNotionRecord();
    const afterTime = afterRecord
      ? new Date(afterRecord.created_time).getTime()
      : 0;

    if (afterTime > beforeTime) {
      const excerpt = afterRecord.properties?.原文片段?.rich_text?.[0]?.text?.content || '';
      const aiConv = afterRecord.properties?.AI对话?.rich_text?.[0]?.text?.content || '';
      const platform = afterRecord.properties?.来源平台?.select?.name || '';

      console.log('\n✅ 测试通过！新记录已保存到Notion');
      console.log(`   平台: ${platform}`);
      console.log(`   原文片段: ${excerpt.slice(0, 80)}...`);
      console.log(`   AI对话长度: ${aiConv.length} 字符`);
      console.log(`   耗时: ${((Date.now() - startTime) / 1000).toFixed(1)}秒`);
      console.log('\n📊 测试结果: PASS ✅');
    } else {
      console.log('\n❌ 测试失败：Notion中未检测到新记录');
      console.log('   采集器日志:', logs);
      console.log('\n📊 测试结果: FAIL ❌');
    }

  } catch (err) {
    console.error('\n❌ 测试出错:', err.message);
    console.log('   采集器日志:', logs);
    console.log('\n📊 测试结果: ERROR ❌');
  } finally {
    await browser.close();
  }
}

runTest().catch(console.error);
