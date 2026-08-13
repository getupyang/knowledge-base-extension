#!/usr/bin/env node
// 2A.2 characterization：content↔background RPC 现状（场景清单 A 组的 background 半边）
// 钉住三路径：成功 / 后端不可用 / 超时，以及"镜像写失败仍报成功"的静默现状。
// 纯 node，chrome/fetch 全 mock，不碰真实服务。

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");

// ── chrome mock ──
const listeners = { onMessage: [] };
const storageData = new Map();
let storageFailure = null;

global.chrome = {
  runtime: {
    onMessage: { addListener: (fn) => listeners.onMessage.push(fn) },
    onInstalled: { addListener: () => {} },
    onStartup: { addListener: () => {} },
  },
  storage: {
    local: {
      get: (keys, cb) => {
        const out = {};
        for (const k of Array.isArray(keys) ? keys : [keys]) {
          if (storageData.has(k)) out[k] = storageData.get(k);
        }
        if (cb) return void cb(out);
        return Promise.resolve(out);
      },
      set: (obj) => {
        if (storageFailure) return Promise.reject(new Error(storageFailure));
        for (const [k, v] of Object.entries(obj)) storageData.set(k, v);
        return Promise.resolve();
      },
    },
  },
  contextMenus: {
    removeAll: (cb) => cb && cb(),
    create: () => {},
    onClicked: { addListener: () => {} },
  },
  tabs: { sendMessage: () => Promise.resolve() },
};

// ── fetch mock：按 URL 路由，可编程 ──
let fetchLog = [];
let fetchImpl = null;
global.fetch = async (url, opts = {}) => {
  fetchLog.push({ url: String(url), opts });
  return fetchImpl(String(url), opts);
};
const jsonResp = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});
// /client-error 诊断上报永远吞掉，与产品语义一致（诊断失败不影响主流程）
const routeClientError = (url) => url.includes("/client-error") ? jsonResp({}) : null;

// ── 加载 background（importScripts → require vault-core）──
global.importScripts = (p) => {
  global.KBVaultCore = require(path.join(ROOT, p));
};
vm.runInThisContext(
  fs.readFileSync(path.join(ROOT, "src", "background", "index.js"), "utf8"),
  { filename: "background/index.js" }
);
assert.strictEqual(listeners.onMessage.length, 1, "onMessage 应注册且仅注册一个监听器");

// 这些消息的处理是异步回包，监听器必须 return true——否则 Chrome 会提前关闭
// sendResponse 通道（Codex review #6：mock 不许吞掉这个契约）
const ASYNC_TYPES = new Set(["VAULT_MIRROR", "SAVE_TO_NOTION", "UPSERT_NOTION_PAGE", "CALL_AI"]);

function send(msg) {
  return new Promise((resolve, reject) => {
    const ret = listeners.onMessage[0](msg, {}, resolve);
    if (ASYNC_TYPES.has(msg.type) && ret !== true) {
      reject(new Error(`${msg.type} 监听器未 return true——真实 Chrome 中异步 sendResponse 会失效`));
    }
  });
}

const realSetTimeout = global.setTimeout;
let failures = 0;
async function check(name, fn) {
  fetchLog = [];
  try {
    await fn();
    console.log(`  [PASS] ${name}`);
  } catch (err) {
    failures += 1;
    console.log(`  [FAIL] ${name}: ${err.message}`);
  } finally {
    global.setTimeout = realSetTimeout;
    storageFailure = null;
  }
}

(async () => {
  console.log("== 2A.2 background RPC characterization ==");

  await check("A0 PING → pong（通道存活探测）", async () => {
    assert.deepStrictEqual(await send({ type: "PING" }), { pong: true });
  });

  await check("A1 保存成功：SAVE_TO_NOTION → 后端 200 → {success:true}", async () => {
    fetchImpl = (url) =>
      routeClientError(url) || jsonResp({ success: true });
    const res = await send({
      type: "SAVE_TO_NOTION",
      data: { title: "t", url: "u", platform: "博客", excerpt: "e", thought: "th" },
    });
    assert.deepStrictEqual(res, { success: true });
    assert.ok(fetchLog[0].url.includes("/captures/save"), "应打到 /captures/save");
  });

  await check("A2 后端不可用：连接被拒 → {success:false} 明确报错（不静默）", async () => {
    fetchImpl = (url) => {
      const ce = routeClientError(url);
      if (ce) return ce;
      throw new Error("Failed to fetch");
    };
    const res = await send({ type: "SAVE_TO_NOTION", data: { title: "t", url: "u" } });
    assert.strictEqual(res.success, false);
    assert.strictEqual(res.error, "Failed to fetch");
  });

  await check("A2b 后端 500：→ {success:false} 错误信息含状态码", async () => {
    fetchImpl = (url) => routeClientError(url) || jsonResp({}, 500);
    const res = await send({ type: "SAVE_TO_NOTION", data: { title: "t", url: "u" } });
    assert.strictEqual(res.success, false);
    assert.ok(res.error.includes("HTTP 500"), `错误应含 HTTP 500，实际：${res.error}`);
  });

  await check("A1b CALL_AI 成功：创建评论 → 轮询到 agent 回复 → 返回内容", async () => {
    global.setTimeout = (fn) => realSetTimeout(fn, 0); // 压缩 3s 轮询间隔
    fetchImpl = (url, opts) => {
      const ce = routeClientError(url);
      if (ce) return ce;
      if (opts.method === "POST") return jsonResp({ id: 42 });
      return jsonResp({ replies: [{ author: "agent", content: "回复内容" }] });
    };
    const res = await send({
      type: "CALL_AI",
      data: { systemPrompt: "sp", messages: [{ content: "问题" }] },
    });
    assert.deepStrictEqual(res, { success: true, reply: "回复内容" });
  });

  await check("A2c CALL_AI 创建失败：后端非 2xx → 明确报'agent_api 创建失败'", async () => {
    fetchImpl = (url) => routeClientError(url) || jsonResp({}, 503);
    const res = await send({ type: "CALL_AI", data: { systemPrompt: "", messages: [] } });
    assert.strictEqual(res.success, false);
    assert.strictEqual(res.error, "agent_api 创建失败");
  });

  await check("A3 CALL_AI 超时：100 次轮询无回复 → 明确报'agent 响应超时'", async () => {
    global.setTimeout = (fn) => realSetTimeout(fn, 0);
    fetchImpl = (url, opts) => {
      const ce = routeClientError(url);
      if (ce) return ce;
      if (opts.method === "POST") return jsonResp({ id: 43 });
      return jsonResp({ replies: [] });
    };
    const res = await send({ type: "CALL_AI", data: { systemPrompt: "", messages: [] } });
    assert.strictEqual(res.success, false);
    assert.strictEqual(res.error, "agent 响应超时");
    const polls = fetchLog.filter((f) => f.url.includes("/comments/43"));
    assert.strictEqual(polls.length, 100, `应轮询 100 次，实际 ${polls.length}`);
  });

  await check("A4a VAULT_MIRROR 成功：镜像写入 storage 且回 {success:true}", async () => {
    fetchImpl = (url) => routeClientError(url) || jsonResp({});
    const res = await send({
      type: "VAULT_MIRROR",
      data: { kind: "comment", pageUrl: "https://x.test/a", records: [] },
    });
    assert.deepStrictEqual(res, { success: true });
    assert.ok(storageData.has("kb_vault_v1"), "storage 中应有 kb_vault_v1 镜像");
  });

  await check("A4b【钉现状】VAULT_MIRROR 存储写失败：仍回 {success:true}（静默，仅诊断上报）", async () => {
    fetchImpl = (url) => routeClientError(url) || jsonResp({});
    storageFailure = "disk full";
    const res = await send({
      type: "VAULT_MIRROR",
      data: { kind: "comment", pageUrl: "https://x.test/b" },
    });
    // 当前产品语义：镜像失败不打扰主流程（background/index.js:5 注释明示）。
    // 若未来改为显式上报失败，本断言应同步更新——它变红即提醒"你改变了静默约定"。
    assert.deepStrictEqual(res, { success: true });
    const reported = fetchLog.some((f) => f.url.includes("/client-error"));
    assert.ok(reported, "失败应有 /client-error 诊断上报");
  });

  console.log(failures === 0 ? "\nAll RPC characterization checks passed." : `\n${failures} check(s) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})();
