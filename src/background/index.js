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

// 启动时从 agent_api 拉取 Notion 配置写入 storage（后端 ~/.kb_config 为唯一配置源）
async function autoLoadConfig() {
  try {
    const resp = await fetch("http://localhost:8766/config");
    if (!resp.ok) return;
    const { notionToken, databaseId } = await resp.json();
    if (notionToken && databaseId) {
      await chrome.storage.local.set({ notionToken, databaseId });
      console.log("[KB] 已从本地服务同步 Notion 配置");
    }
  } catch { /* 后端未启动，静默跳过 */ }
}

chrome.runtime.onStartup.addListener(autoLoadConfig);
chrome.runtime.onInstalled.addListener(autoLoadConfig);

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
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (msg.type === "UPSERT_NOTION_PAGE") {
    upsertNotionPage(msg.data)
      .then(pageId => sendResponse({ success: true, pageId }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (msg.type === "CALL_AI") {
    callAIViaAgent(msg.data.systemPrompt, msg.data.messages)
      .then(reply => sendResponse({ success: true, reply }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
});

// 通过 localhost:8766 调 claude -p（质量更好，有 Notion 记忆注入）
async function callAIViaAgent(systemPrompt, msgs) {
  const AGENT_API = 'http://localhost:8766';
  const userMsg = msgs[msgs.length - 1]?.content || '';
  // 把 systemPrompt 作为 comment 内容发过去（8766 会注入 Notion 记忆 + 项目上下文）
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

// 每条划线评论对应一个 Notion page：有 pageId 则更新，无则新建
async function upsertNotionPage({ notionPageId, title, url, platform, excerpt, thought, aiConversation }) {
  const { NOTION_TOKEN, DATABASE_ID } = await getConfig();
  if (!NOTION_TOKEN) throw new Error("请先在插件设置中配置 Notion Token");
  if (!DATABASE_ID) throw new Error("请先在插件设置中配置 Notion Database ID");

  const headers = {
    "Authorization": `Bearer ${NOTION_TOKEN}`,
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
  };

  // 只有当存在 AI 回复时才写 评论区对话 字段（避免第一条纯用户评论重复存两份）
  const hasAIContent = aiConversation && aiConversation.includes("AI:");
  const conversationField = hasAIContent
    ? { 评论区对话: { rich_text: splitRichText(aiConversation) } }
    : {};

  if (notionPageId) {
    // 更新已有 page
    const res = await fetch(`https://api.notion.com/v1/pages/${notionPageId}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify({
        properties: {
          我的想法: { rich_text: splitRichText(thought) },
          ...conversationField
        }
      })
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.message || "Notion 更新失败"); }
    return notionPageId;
  } else {
    // 新建 page（首次提交评论时创建，不含对话记录）
    const body = {
      parent: { database_id: DATABASE_ID },
      properties: {
        标题: { title: [{ text: { content: truncate(title, 100) } }] },
        来源平台: { select: { name: platform } },
        来源URL: { url },
        原文片段: { rich_text: splitRichText(excerpt) },
        我的想法: { rich_text: splitRichText(thought) },
        ...conversationField
      }
    };
    const res = await fetch("https://api.notion.com/v1/pages", {
      method: "POST", headers, body: JSON.stringify(body)
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.message || "Notion API错误"); }
    const data = await res.json();
    return data.id;
  }
}

async function saveToNotion({ title, url, platform, excerpt, thought, aiConversation }) {
  const { NOTION_TOKEN, DATABASE_ID } = await getConfig();
  if (!NOTION_TOKEN) throw new Error("请先在插件设置中配置 Notion Token");
  if (!DATABASE_ID) throw new Error("请先在插件设置中配置 Notion Database ID");
  const body = {
    parent: { database_id: DATABASE_ID },
    properties: {
      标题: { title: [{ text: { content: truncate(title, 100) } }] },
      来源平台: { select: { name: platform } },
      来源URL: { url: url },
      原文片段: { rich_text: splitRichText(excerpt) },
      我的想法: { rich_text: splitRichText(thought) },
      评论区对话: { rich_text: splitRichText(aiConversation) }
    }
  };

  const res = await fetch("https://api.notion.com/v1/pages", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${NOTION_TOKEN}`,
      "Content-Type": "application/json",
      "Notion-Version": "2022-06-28"
    },
    body: JSON.stringify(body)
  });

  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.message || "Notion API错误");
  }
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
