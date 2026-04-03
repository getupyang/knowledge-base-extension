const CONFIG = {
  NOTION_TOKEN: "NOTION_TOKEN_REMOVED",
  DATABASE_ID: "DATABASE_ID_REMOVED",
  OPENROUTER_KEY: "OPENROUTER_KEY_REMOVED",
  OPENROUTER_MODEL: "openai/gpt-4o-mini"
};

async function getConfig() {
  return CONFIG;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "save-to-notion",
    title: "保存到知识库",
    contexts: ["selection"]
  });
  chrome.contextMenus.create({
    id: "open-ai-chat",
    title: "AI对话后保存",
    contexts: ["selection"]
  });
  chrome.contextMenus.create({
    id: "add-comment",
    title: "💬 评论",
    contexts: ["selection"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const selectedText = info.selectionText;
  const pageUrl = tab.url;
  const pageTitle = tab.title;
  const platform = detectPlatform(pageUrl);

  if (info.menuItemId === "save-to-notion") {
    // 发消息给content script显示弹框，通过sendResponse回传结果
    chrome.tabs.sendMessage(tab.id, {
      type: "SHOW_INPUT_DIALOG",
      excerpt: selectedText,
      title: pageTitle,
      url: pageUrl,
      platform
    }, async (response) => {
      if (!response) return; // 用户取消
      const thought = response.thought || "";
      try {
        await saveToNotion({ title: pageTitle, url: pageUrl, platform, excerpt: selectedText, thought, aiConversation: "" });
        chrome.tabs.sendMessage(tab.id, { type: "SAVE_SUCCESS" }).catch(() => {});
      } catch (err) {
        chrome.tabs.sendMessage(tab.id, { type: "SAVE_ERROR", error: err.message }).catch(() => {});
      }
    });
  }

  if (info.menuItemId === "open-ai-chat") {
    chrome.tabs.sendMessage(tab.id, {
      type: "OPEN_CHAT_PANEL",
      context: { excerpt: selectedText, title: pageTitle, url: pageUrl, platform }
    }).catch(() => {});
  }

  if (info.menuItemId === "add-comment") {
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

async function callAI(systemPrompt, msgs) {
  const { OPENROUTER_KEY, OPENROUTER_MODEL } = await getConfig();
  if (!OPENROUTER_KEY) throw new Error("请先在插件设置中配置 OpenRouter API Key");
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${OPENROUTER_KEY}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "chrome-extension://knowledge-base",
      "X-Title": "Knowledge Base Assistant"
    },
    body: JSON.stringify({
      model: OPENROUTER_MODEL,
      messages: [{ role: "system", content: systemPrompt }, ...msgs]
    })
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || "AI请求失败");
  }
  const data = await res.json();
  return data.choices[0].message.content;
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
      AI对话: { rich_text: splitRichText(aiConversation) }
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
