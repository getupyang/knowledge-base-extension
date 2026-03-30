const MODEL = "anthropic/claude-3.5-sonnet";

let messages = [];
let currentContext = null;
let panelEl = null;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SHOW_INPUT_DIALOG") {
    showInputDialog(msg.excerpt, msg.title, msg.url, msg.platform, (response) => {
      sendResponse(response);
    });
    return true; // 保持异步channel
  } else if (msg.type === "OPEN_CHAT_PANEL") {
    openChatPanel(msg.context);
  } else if (msg.type === "SAVE_SUCCESS") {
    showToast("✓ 已保存到知识库", "success");
  } else if (msg.type === "SAVE_ERROR") {
    showToast("✗ 保存失败：" + msg.error, "error");
  }
});

// ─── 聊天侧边栏 ────────────────────────────────────────────

function openChatPanel(context) {
  currentContext = context;
  messages = [];

  // 已有panel则更新内容
  if (panelEl) {
    updatePanelContext();
    return;
  }

  // 注入样式
  if (!document.getElementById("kb-panel-style")) {
    const style = document.createElement("style");
    style.id = "kb-panel-style";
    style.textContent = `
      #kb-panel {
        position: fixed;
        top: 0; right: 0;
        width: 380px;
        height: 100vh;
        background: #fff;
        box-shadow: -4px 0 24px rgba(0,0,0,0.12);
        z-index: 2147483647;
        display: flex;
        flex-direction: column;
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        font-size: 14px;
        color: #333;
      }
      #kb-panel * { box-sizing: border-box; }
      #kb-panel-header {
        padding: 12px 16px;
        border-bottom: 1px solid #eee;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-shrink: 0;
      }
      #kb-panel-header h2 { font-size: 14px; font-weight: 600; }
      #kb-panel-actions { display: flex; gap: 8px; align-items: center; }
      #kb-save-btn {
        padding: 5px 12px;
        background: #10b981;
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 12px;
        font-weight: 500;
      }
      #kb-save-btn:disabled { background: #ccc; cursor: not-allowed; }
      #kb-close-btn {
        background: none;
        border: none;
        cursor: pointer;
        font-size: 18px;
        color: #999;
        padding: 0 4px;
        line-height: 1;
      }
      #kb-excerpt {
        margin: 12px 16px 0;
        padding: 10px 12px;
        background: #f8f8f8;
        border-radius: 8px;
        font-size: 12px;
        color: #555;
        line-height: 1.6;
        border-left: 3px solid #10b981;
        flex-shrink: 0;
      }
      #kb-excerpt-source {
        font-size: 11px;
        color: #aaa;
        margin: 4px 16px 8px;
        flex-shrink: 0;
      }
      #kb-chat-area {
        flex: 1;
        overflow-y: auto;
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-height: 0;
      }
      .kb-msg { display: flex; flex-direction: column; gap: 3px; }
      .kb-msg-label { font-size: 11px; color: #aaa; font-weight: 500; }
      .kb-msg-bubble {
        padding: 9px 12px;
        border-radius: 8px;
        line-height: 1.6;
        font-size: 13px;
        white-space: pre-wrap;
        word-break: break-word;
        user-select: text;
        -webkit-user-select: text;
        cursor: text;
      }
      .kb-msg.user .kb-msg-bubble {
        background: #f0fdf4;
        border: 1px solid #d1fae5;
        color: #065f46;
      }
      .kb-msg.assistant .kb-msg-bubble {
        background: #f8f9fa;
        border: 1px solid #eee;
        color: #333;
      }
      .kb-msg.loading .kb-msg-bubble { color: #aaa; }
      #kb-input-area {
        padding: 12px 16px;
        border-top: 1px solid #eee;
        display: flex;
        gap: 8px;
        align-items: flex-end;
        flex-shrink: 0;
      }
      #kb-user-input {
        flex: 1;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 13px;
        font-family: inherit;
        resize: none;
        height: 60px;
        outline: none;
        line-height: 1.5;
        color: #333;
        background: #fff;
      }
      #kb-user-input:focus { border-color: #10b981; }
      #kb-send-btn {
        padding: 8px 14px;
        background: #333;
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 13px;
        height: 60px;
      }
      #kb-send-btn:disabled { background: #ccc; cursor: not-allowed; }
    `;
    document.head.appendChild(style);
  }

  panelEl = document.createElement("div");
  panelEl.id = "kb-panel";
  panelEl.innerHTML = `
    <div id="kb-panel-header">
      <h2>知识库助手</h2>
      <div id="kb-panel-actions">
        <button id="kb-save-btn" disabled>保存到Notion</button>
        <button id="kb-close-btn">×</button>
      </div>
    </div>
    <div id="kb-excerpt"></div>
    <div id="kb-excerpt-source"></div>
    <div id="kb-chat-area"></div>
    <div id="kb-input-area">
      <textarea id="kb-user-input" placeholder="输入问题（Cmd+Enter发送）"></textarea>
      <button id="kb-send-btn">发送</button>
    </div>
  `;
  document.body.appendChild(panelEl);

  // 事件绑定
  document.getElementById("kb-close-btn").addEventListener("click", () => {
    panelEl.remove();
    panelEl = null;
  });
  document.getElementById("kb-send-btn").addEventListener("click", sendMessage);
  document.getElementById("kb-user-input").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendMessage();
  });
  document.getElementById("kb-save-btn").addEventListener("click", saveConversation);

  updatePanelContext();
}

function updatePanelContext() {
  document.getElementById("kb-excerpt").textContent = `「${truncate(currentContext.excerpt, 200)}」`;
  document.getElementById("kb-excerpt-source").textContent = `${currentContext.platform} · ${truncate(currentContext.title, 50)}`;
  document.getElementById("kb-chat-area").innerHTML = "";
  document.getElementById("kb-save-btn").disabled = true;
  const input = document.getElementById("kb-user-input");
  if (input) { input.disabled = false; input.focus(); }
}

async function sendMessage() {
  const input = document.getElementById("kb-user-input");
  const sendBtn = document.getElementById("kb-send-btn");
  const text = input.value.trim();
  if (!text || !currentContext) return;

  input.value = "";
  input.style.height = "60px";
  input.disabled = true;
  sendBtn.disabled = true;

  appendMessage("user", text);
  messages.push({ role: "user", content: text });

  const loadingEl = appendMessage("assistant", "思考中...", true);

  const systemPrompt = `你是用户的思考伙伴，帮助用户深度理解和延伸阅读内容。

用户正在阅读：
来源：${currentContext.platform} - ${currentContext.title}
链接：${currentContext.url}
原文片段：「${currentContext.excerpt}」

要求：
- 回答要有深度，不要为了简洁牺牲质量
- 主动补充原文没有提到但高度相关的背景知识
- 如果原文的观点值得挑战或有局限性，直接指出
- 用中文回答`;

  try {
    const reply = await callAI(systemPrompt, messages);
    loadingEl.classList.remove("loading");
    loadingEl.querySelector(".kb-msg-bubble").textContent = reply;
    messages.push({ role: "assistant", content: reply });
    document.getElementById("kb-save-btn").disabled = false;
  } catch (err) {
    loadingEl.querySelector(".kb-msg-bubble").textContent = "出错了：" + err.message;
  }

  input.disabled = false;
  sendBtn.disabled = false;
  input.focus();
}

async function callAI(systemPrompt, msgs) {
  const response = await chrome.runtime.sendMessage({
    type: "CALL_AI",
    data: { systemPrompt, messages: msgs }
  });
  if (response?.success) {
    return response.reply;
  }
  throw new Error(response?.error || "AI请求失败");
}

function appendMessage(role, text, isLoading = false) {
  const chatArea = document.getElementById("kb-chat-area");
  const div = document.createElement("div");
  div.className = `kb-msg ${role}${isLoading ? " loading" : ""}`;
  div.innerHTML = `
    <div class="kb-msg-label">${role === "user" ? "我" : "AI"}</div>
    <div class="kb-msg-bubble">${escapeHtml(text)}</div>
  `;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
  return div;
}

async function saveConversation() {
  if (!currentContext || messages.length === 0) return;
  const saveBtn = document.getElementById("kb-save-btn");
  saveBtn.disabled = true;
  saveBtn.textContent = "保存中...";

  const aiConversation = messages.map(m =>
    `${m.role === "user" ? "Q" : "A"}: ${m.content}`
  ).join("\n\n");

  try {
    const response = await chrome.runtime.sendMessage({
      type: "SAVE_TO_NOTION",
      data: {
        title: currentContext.title,
        url: currentContext.url,
        platform: currentContext.platform,
        excerpt: currentContext.excerpt,
        thought: "",
        aiConversation
      }
    });
    if (response?.success) {
      saveBtn.textContent = "✓ 已保存";
      setTimeout(() => { saveBtn.textContent = "保存到Notion"; saveBtn.disabled = false; }, 2000);
    } else {
      throw new Error(response?.error || "未知错误");
    }
  } catch (err) {
    console.error("保存失败详情:", err);
    saveBtn.textContent = "保存失败";
    saveBtn.disabled = false;
  }
}

// ─── 快速保存弹框 ────────────────────────────────────────────

function showInputDialog(excerpt, title, url, platform, callback) {
  const existing = document.getElementById("kb-dialog");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "kb-dialog";
  overlay.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    z-index: 2147483646; background: rgba(0,0,0,0.3);
    display: flex; align-items: center; justify-content: center;
  `;

  const box = document.createElement("div");
  box.style.cssText = `
    background: white; border-radius: 12px; padding: 20px;
    width: 420px; max-width: 90vw;
    box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    font-family: -apple-system, sans-serif;
  `;
  box.innerHTML = `
    <div style="font-size:12px;color:#888;margin-bottom:8px;">${platform} · ${truncate(title, 40)}</div>
    <div style="font-size:13px;color:#444;background:#f8f8f8;padding:10px;border-radius:6px;margin-bottom:12px;line-height:1.5;max-height:80px;overflow:hidden;">
      「${truncate(excerpt, 120)}」
    </div>
    <textarea id="kb-thought-input" placeholder="你的想法（可留空，Cmd+Enter保存）" style="
      width:100%; box-sizing:border-box; height:80px;
      border:1px solid #e0e0e0; border-radius:6px;
      padding:8px 10px; font-size:14px; font-family:inherit;
      resize:none; outline:none; line-height:1.5;
      background:#fff; color:#333;
    "></textarea>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:10px;">
      <button id="kb-dialog-cancel" style="padding:7px 16px;border:1px solid #e0e0e0;border-radius:6px;background:white;cursor:pointer;font-size:13px;color:#666;">取消</button>
      <button id="kb-dialog-save" style="padding:7px 16px;border:none;border-radius:6px;background:#10b981;color:white;cursor:pointer;font-size:13px;font-weight:500;">保存</button>
    </div>
  `;
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  const textarea = box.querySelector("#kb-thought-input");
  setTimeout(() => textarea.focus(), 50);

  const doSave = () => { overlay.remove(); callback({ thought: textarea.value.trim() }); };
  const doCancel = () => { overlay.remove(); callback(null); };

  box.querySelector("#kb-dialog-save").addEventListener("click", doSave);
  box.querySelector("#kb-dialog-cancel").addEventListener("click", doCancel);
  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") doSave();
    if (e.key === "Escape") doCancel();
  });
  // 不允许点击遮罩关闭，避免误触
}

// ─── 工具函数 ────────────────────────────────────────────────

function showToast(text, type) {
  const existing = document.getElementById("kb-toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.id = "kb-toast";
  toast.textContent = text;
  toast.style.cssText = `
    position:fixed; bottom:24px; right:24px; z-index:2147483647;
    padding:12px 20px; border-radius:8px; font-size:14px;
    font-family:-apple-system,sans-serif; color:white;
    background:${type === "success" ? "#10b981" : "#ef4444"};
    box-shadow:0 4px 12px rgba(0,0,0,0.15); transition:opacity 0.3s;
  `;
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 3000);
}

function truncate(str, max) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "..." : str;
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
}
