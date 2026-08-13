importScripts("/src/common/vault-core.js");

// ── 本地批注库（vault）镜像 ──
// content script 每次保存批注/高亮后发 VAULT_MIRROR，聚合写入 chrome.storage.local，
// 供 popup 统计与导出。串行写链避免并发覆盖；失败只上报诊断，不影响主流程。
const VAULT_KEY = "kb_vault_v1";
let _vaultWriteChain = Promise.resolve();
function mirrorToVault(data) {
  _vaultWriteChain = _vaultWriteChain
    .then(async () => {
      const stored = await chrome.storage.local.get(VAULT_KEY);
      const next = KBVaultCore.applyMirror(stored[VAULT_KEY], {
        ...data,
        mirroredAt: new Date().toISOString(),
      });
      await chrome.storage.local.set({ [VAULT_KEY]: next });
    })
    .catch((err) => {
      reportClientError("vaultMirror", err, { pageUrl: data?.pageUrl, kind: data?.kind });
    });
  return _vaultWriteChain;
}

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["notionToken", "databaseId"], (result) => {
      resolve({
        NOTION_TOKEN: result.notionToken || "",
        DATABASE_ID: result.databaseId || ""
      });
    });
  });
}

// 启动时从 agent_api 拉取非敏感运行状态；密钥只留在后端 ~/.kb_config。
async function autoLoadConfig() {
  try {
    const resp = await kbEngineFetch("http://localhost:8766/config");
    if (!resp.ok) return;
    const { storageMode, notionConfigured } = await resp.json();
    await chrome.storage.local.set({ storageMode, notionConfigured: !!notionConfigured });
    console.log("[KB] 已从本地服务同步运行状态");
  } catch { /* 后端未启动，静默跳过 */ }
}

chrome.runtime.onStartup.addListener(autoLoadConfig);
chrome.runtime.onInstalled.addListener(autoLoadConfig);

// 把插件侧失败上报给本地后端，便于排查。失败静默，不阻塞主流程。
async function reportClientError(source, err, context = {}) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 3000);
    await kbEngineFetch("http://localhost:8766/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source,
        message: (err && err.message) ? String(err.message) : String(err),
        stack: (err && err.stack) ? String(err.stack) : "",
        context,
        ts: new Date().toISOString()
      }),
      signal: ctrl.signal
    });
    clearTimeout(t);
  } catch { /* 诊断失败绝不影响主流程 */ }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "kb-highlight",
      title: "🖊️ 高亮保存",
      contexts: ["selection"]
    });
    chrome.contextMenus.create({
      id: "kb-comment",
      title: "💬 评论",
      contexts: ["selection"]
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const selectedText = info.selectionText;
  const pageUrl = tab.url;
  const pageTitle = tab.title;
  const platform = detectPlatform(pageUrl);

  if (info.menuItemId === "kb-highlight") {
    chrome.tabs.sendMessage(tab.id, {
      type: "HIGHLIGHT_AND_SAVE",
      excerpt: selectedText,
      title: pageTitle,
      url: pageUrl,
      platform
    }).catch(() => {});
  }

  if (info.menuItemId === "kb-comment") {
    chrome.tabs.sendMessage(tab.id, {
      type: "ADD_COMMENT",
      excerpt: selectedText,
      title: pageTitle,
      url: pageUrl,
      platform
    }).catch(() => {});
  }
});

// ── 统一 API 代理（清单 3.2）──
// content script 不再直连本地引擎：所有请求经此单一咽喉转发。
// 网页上下文的直连迫使后端 CORS 全开；改道后 CORS 可收紧到扩展来源（3.1），
// token 附加也只需改这一处（3.3）。
const KB_API_BASE = "http://localhost:8766";

// ── 配对 token（清单 3.3，认证协议 §2-§4）──
// 引擎 token 存 chrome.storage.local；缺失或失效时经 POST /pair 自动领取，用户零操作。
const PAIR_TOKEN_KEY = "margin_pair_token";
let _pairTokenCache = null;

async function getPairToken(forceRenew = false) {
  if (!forceRenew) {
    if (_pairTokenCache) return _pairTokenCache;
    const stored = await chrome.storage.local.get(PAIR_TOKEN_KEY);
    if (stored[PAIR_TOKEN_KEY]) {
      _pairTokenCache = stored[PAIR_TOKEN_KEY];
      return _pairTokenCache;
    }
  }
  try {
    const resp = await fetch(KB_API_BASE + "/pair", { method: "POST" });
    if (resp.ok) {
      const data = await resp.json();
      if (data && data.token) {
        _pairTokenCache = data.token;
        await chrome.storage.local.set({ [PAIR_TOKEN_KEY]: data.token });
      }
    }
  } catch { /* 引擎未启动：无 token 继续（强制开关关闭时后端不校验） */ }
  return _pairTokenCache;
}

// 所有对引擎的请求走这里：附加 X-Margin-Token；401 时重新配对再试一次（token 轮换自愈）
async function kbEngineFetch(url, options = {}) {
  const token = await getPairToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers["X-Margin-Token"] = token;
  let resp = await fetch(url, { ...options, headers });
  if (resp.status === 401) {
    const fresh = await getPairToken(true);
    if (fresh && fresh !== token) {
      resp = await fetch(url, { ...options, headers: { ...headers, "X-Margin-Token": fresh } });
    }
  }
  return resp;
}

async function apiProxy(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 20000);
  try {
    const resp = await kbEngineFetch(KB_API_BASE + path, {
      method: options.method || "GET",
      headers: options.headers || undefined,
      body: options.body ?? undefined,
      signal: ctrl.signal,
    });
    const bodyText = await resp.text();
    return { ok: resp.ok, status: resp.status, statusText: resp.statusText, bodyText };
  } finally {
    clearTimeout(timer);
  }
}

// 统一消息处理
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 认证协议 §6：只处理本扩展发来的消息（配合 manifest 不声明 externally_connectable）
  if (!sender || sender.id !== chrome.runtime.id) {
    return;
  }
  if (msg.type === "API_FETCH") {
    apiProxy(msg.path, msg.options || {})
      .then(sendResponse)
      .catch((err) => sendResponse({ __error: (err && err.message) || String(err) }));
    return true;
  }
  if (msg.type === "RELOAD_CONFIG") {
    // 不再需要缓存，每次调用时实时读取
    sendResponse({ success: true });
    return;
  }
  if (msg.type === "PING") {
    sendResponse({ pong: true });
    return;
  }
  if (msg.type === "VAULT_MIRROR") {
    mirrorToVault(msg.data || {}).then(() => sendResponse({ success: true }));
    return true;
  }
  if (msg.type === "SAVE_TO_NOTION") {
    saveToNotion(msg.data)
      .then(() => sendResponse({ success: true }))
      .catch(err => {
        reportClientError("saveCapture", err, { url: msg.data?.url, title: msg.data?.title });
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
  if (msg.type === "UPSERT_NOTION_PAGE") {
    upsertNotionPage(msg.data)
      .then(pageId => sendResponse({ success: true, pageId }))
      .catch(err => {
        reportClientError("upsertCapture", err, {
          url: msg.data?.url,
          notionPageId: msg.data?.notionPageId,
          hasAI: !!(msg.data?.aiConversation)
        });
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
  if (msg.type === "CALL_AI") {
    callAIViaAgent(msg.data.systemPrompt, msg.data.messages)
      .then(reply => sendResponse({ success: true, reply }))
      .catch(err => {
        reportClientError("callAIViaAgent", err, {
          msg_count: msg.data?.messages?.length || 0
        });
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
});

// 通过 localhost:8766 调本地 agent（质量更好，有本地记忆注入）
async function callAIViaAgent(systemPrompt, msgs) {
  const AGENT_API = 'http://localhost:8766';
  const userMsg = msgs[msgs.length - 1]?.content || '';
  // 把 systemPrompt 作为 comment 内容发过去（8766 会注入本地记忆 + 项目上下文）
  const createRes = await kbEngineFetch(`${AGENT_API}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      page_url: 'chrome-extension://comment',
      page_title: '插件评论',
      selected_text: systemPrompt.match(/用户划线内容[：:][「「]?([^」\n]+)/)?.[1] || '',
      comment: userMsg
    })
  });
  if (!createRes.ok) throw new Error('agent_api 创建失败');
  const { id } = await createRes.json();

  // 轮询最多 5 分钟，每 3 秒一次
  for (let i = 0; i < 100; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const pollRes = await kbEngineFetch(`${AGENT_API}/comments/${id}`);
    const data = await pollRes.json();
    const agentReply = data.replies?.find(r => r.author === 'agent');
    if (agentReply) return agentReply.content;
  }
  throw new Error('agent 响应超时');
}


function splitRichText(str, max = 1990) {
  if (!str) return [{ text: { content: "" } }];
  const chunks = [];
  for (let i = 0; i < str.length; i += max) {
    chunks.push({ text: { content: str.slice(i, i + max) } });
  }
  // Notion最多100个rich_text块
  return chunks.slice(0, 100);
}

// 旧消息名保留兼容；真实写入统一走本地 capture endpoint，Notion 只是后端可选备份。
async function upsertNotionPage({ notionPageId, title, url, platform, excerpt, thought, aiConversation }) {
  const res = await kbEngineFetch("http://localhost:8766/captures/upsert", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({notionPageId, title, url, platform, excerpt, thought, aiConversation})
  });
  if (!res.ok) throw new Error(`本地 capture 保存失败：HTTP ${res.status}`);
  const data = await res.json();
  if (!data.success) throw new Error(data.detail || data.error || "本地 capture 保存失败");
  return data.pageId || data.localCommentId || null;
}

async function saveToNotion({ title, url, platform, excerpt, thought, aiConversation }) {
  const res = await kbEngineFetch("http://localhost:8766/captures/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({title, url, platform, excerpt, thought, aiConversation})
  });
  if (!res.ok) throw new Error(`本地 capture 保存失败：HTTP ${res.status}`);
  const data = await res.json();
  if (!data.success) throw new Error(data.detail || data.error || "本地 capture 保存失败");
}

function detectPlatform(url) {
  if (url.includes("youtube.com") || url.includes("youtu.be")) return "YouTube";
  if (url.includes("substack.com")) return "博客";
  if (url.includes("mp.weixin.qq.com")) return "公众号";
  return "博客";
}

function truncate(str, max) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "..." : str;
}
