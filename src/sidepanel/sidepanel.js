const OPENROUTER_KEY = "OPENROUTER_KEY_HERE";
const NOTION_TOKEN = "NOTION_TOKEN_HERE";
const DATABASE_ID = "32dae139524480ecbeb4fb76b0269245";
const MODEL = "anthropic/claude-3.5-sonnet";

let currentContext = null; // { excerpt, title, url, platform }
let messages = []; // 对话历史

// DOM
const excerptBox = document.getElementById("excerpt-box");
const excerptSource = document.getElementById("excerpt-source");
const emptyState = document.getElementById("empty-state");
const chatArea = document.getElementById("chat-area");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const saveBtn = document.getElementById("save-btn");

// 页面加载时从storage读取上下文
chrome.storage.session.get("sidepanelContext", (data) => {
  if (data.sidepanelContext) {
    setContext(data.sidepanelContext);
  }
});

// 监听storage变化（用户再次右键选新内容时更新）
chrome.storage.session.onChanged.addListener((changes) => {
  if (changes.sidepanelContext?.newValue) {
    setContext(changes.sidepanelContext.newValue);
  }
});

function setContext(context) {
  currentContext = context;
  messages = [];

  // 显示原文
  excerptBox.style.display = "block";
  excerptBox.textContent = `「${truncate(context.excerpt, 200)}」`;
  excerptSource.style.display = "block";
  excerptSource.textContent = `${context.platform} · ${truncate(context.title, 50)}`;

  // 切换到对话区
  emptyState.style.display = "none";
  chatArea.style.display = "flex";
  chatArea.innerHTML = "";

  // 启用输入
  userInput.disabled = false;
  sendBtn.disabled = false;
  userInput.focus();

  saveBtn.disabled = true;
}

// 发送消息
async function sendMessage() {
  const text = userInput.value.trim();
  if (!text || !currentContext) return;

  userInput.value = "";
  userInput.style.height = "60px";
  sendBtn.disabled = true;
  userInput.disabled = true;

  // 添加用户消息到UI
  appendMessage("user", text);

  // 构建消息历史
  messages.push({ role: "user", content: text });

  // 系统提示：带入原文上下文
  const systemPrompt = `你是用户的思考伙伴。用户正在阅读以下内容：

来源：${currentContext.platform} - ${currentContext.title}
链接：${currentContext.url}
原文片段：「${currentContext.excerpt}」

请基于这个上下文和用户对话，帮助用户深入思考和理解。回答简洁有力，不要过度解释。`;

  // loading状态
  const loadingEl = appendMessage("assistant", "思考中...", true);

  try {
    const reply = await callAI(systemPrompt, messages);

    // 替换loading
    loadingEl.classList.remove("loading");
    loadingEl.querySelector(".msg-bubble").textContent = reply;

    messages.push({ role: "assistant", content: reply });
    saveBtn.disabled = false;
  } catch (err) {
    loadingEl.querySelector(".msg-bubble").textContent = "出错了：" + err.message;
  }

  sendBtn.disabled = false;
  userInput.disabled = false;
  userInput.focus();
}

async function callAI(systemPrompt, msgs) {
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${OPENROUTER_KEY}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "chrome-extension://knowledge-base",
      "X-Title": "知识库助手"
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        { role: "system", content: systemPrompt },
        ...msgs
      ]
    })
  });

  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || "AI请求失败");
  }

  const data = await res.json();
  return data.choices[0].message.content;
}

function appendMessage(role, text, isLoading = false) {
  const div = document.createElement("div");
  div.className = `msg ${role}${isLoading ? " loading" : ""}`;
  div.innerHTML = `
    <div class="msg-label">${role === "user" ? "我" : "AI"}</div>
    <div class="msg-bubble">${escapeHtml(text)}</div>
  `;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
  return div;
}

// 保存到Notion
saveBtn.addEventListener("click", async () => {
  if (!currentContext || messages.length === 0) return;

  saveBtn.disabled = true;
  saveBtn.textContent = "保存中...";

  // 格式化对话
  const aiConversation = messages.map(m =>
    `${m.role === "user" ? "Q" : "A"}: ${m.content}`
  ).join("\n\n");

  try {
    await saveToNotion({
      title: currentContext.title,
      url: currentContext.url,
      platform: currentContext.platform,
      excerpt: currentContext.excerpt,
      thought: "",
      aiConversation
    });

    saveBtn.textContent = "✓ 已保存";
    setTimeout(() => {
      saveBtn.textContent = "保存到Notion";
      saveBtn.disabled = false;
    }, 2000);
  } catch (err) {
    saveBtn.textContent = "保存失败";
    saveBtn.disabled = false;
    console.error(err);
  }
});

async function saveToNotion({ title, url, platform, excerpt, thought, aiConversation }) {
  const body = {
    parent: { database_id: DATABASE_ID },
    properties: {
      标题: {
        title: [{ text: { content: truncate(title, 100) } }]
      },
      来源平台: {
        select: { name: platform }
      },
      来源URL: {
        url: url
      },
      原文片段: {
        rich_text: [{ text: { content: truncate(excerpt, 2000) } }]
      },
      我的想法: {
        rich_text: [{ text: { content: truncate(thought, 2000) } }]
      },
      AI对话: {
        rich_text: [{ text: { content: truncate(aiConversation, 2000) } }]
      }
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

// 发送按钮
sendBtn.addEventListener("click", sendMessage);

// Cmd+Enter发送
userInput.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendMessage();
});

// 自动调整输入框高度
userInput.addEventListener("input", () => {
  userInput.style.height = "60px";
  userInput.style.height = Math.min(userInput.scrollHeight, 120) + "px";
});

function truncate(str, max) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "..." : str;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br>");
}
