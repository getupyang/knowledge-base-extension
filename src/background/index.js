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
    const resp = await fetch("http://localhost:8766/config");
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
    await fetch("http://localhost:8766/client-error", {
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

// 统一消息处理
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "RELOAD_CONFIG") {
    // 不再需要缓存，每次调用时实时读取
    sendResponse({ success: true });
    return;
  }
  if (msg.type === "PING") {
    sendResponse({ pong: true });
    return;
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
  const createRes = await fetch(`${AGENT_API}/comments`, {
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
    const pollRes = await fetch(`${AGENT_API}/comments/${id}`);
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
  const res = await fetch("http://localhost:8766/captures/upsert", {
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
  const res = await fetch("http://localhost:8766/captures/save", {
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
