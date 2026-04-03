const MODEL = "anthropic/claude-3.5-sonnet";

let messages = [];
let currentContext = null;
let panelEl = null;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "ADD_COMMENT") {
    commentSystem.open(msg.excerpt, msg.url, msg.title);
    sendResponse({ ok: true });
    return;
  }
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

// ─── 评论系统 ────────────────────────────────────────────────

const commentSystem = (() => {
  const STORAGE_KEY = () => "kb_comments_" + location.href.split("?")[0];
  let panelEl = null;
  let currentExcerpt = "";

  // ── 持久化 ──
  function load() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY()) || "[]"); } catch { return []; }
  }
  function save(comments) {
    localStorage.setItem(STORAGE_KEY(), JSON.stringify(comments));
  }
  function addComment(excerpt, text) {
    const comments = load();
    const c = { id: Date.now(), excerpt, text, createdAt: new Date().toISOString(), replies: [] };
    comments.unshift(c);
    save(comments);
    return c;
  }
  function addReply(commentId, replyText, isAI) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return;
    c.replies.push({ id: Date.now(), text: replyText, isAI, createdAt: new Date().toISOString() });
    save(comments);
    return c;
  }

  // ── 高亮 ──
  function highlightSelection(excerpt) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    const mark = document.createElement("mark");
    mark.className = "kb-comment-highlight";
    mark.style.cssText = "background:#fef08a;border-radius:2px;cursor:pointer;padding:1px 0;";
    mark.title = "点击查看评论";
    try {
      range.surroundContents(mark);
      // 点击高亮：打开面板，不重新高亮（防止自我破坏）
      mark.addEventListener("click", (e) => {
        e.stopPropagation();
        currentExcerpt = excerpt;
        buildPanel();
        panelEl.classList.remove("kb-hidden");
        render();
        // 滚动到对应评论卡片
        const comments = load();
        const match = comments.find(c => c.excerpt === excerpt);
        if (match) {
          setTimeout(() => {
            const card = document.getElementById("kb-cmt-" + match.id);
            if (card) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }, 100);
        }
      });
    } catch (e) {
      // 跨节点选区无法 surroundContents，忽略
    }
    sel.removeAllRanges();
  }

  // ── 注入样式 ──
  function injectStyles() {
    if (document.getElementById("kb-comment-style")) return;
    const s = document.createElement("style");
    s.id = "kb-comment-style";
    s.textContent = `
      #kb-comment-panel {
        position: fixed; top: 0; right: 0; width: 360px; height: 100vh;
        background: #fafafa; border-left: 1px solid #e8e8e8;
        display: flex; flex-direction: column; z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
        font-size: 14px; color: #333; box-shadow: -4px 0 20px rgba(0,0,0,0.08);
        transform: translateX(0); transition: transform 0.25s ease;
      }
      #kb-comment-panel.kb-hidden { transform: translateX(100%); }
      #kb-cp-header {
        padding: 14px 16px; border-bottom: 1px solid #e8e8e8;
        display: flex; align-items: center; justify-content: space-between;
        background: white; flex-shrink: 0;
      }
      #kb-cp-header h3 { font-size: 13px; font-weight: 600; margin: 0; }
      #kb-cp-close {
        background: none; border: 1px solid #e0e0e0; border-radius: 6px;
        padding: 3px 10px; font-size: 12px; cursor: pointer; color: #666;
      }
      #kb-cp-close:hover { border-color: #6366f1; color: #6366f1; }
      #kb-cp-body { flex: 1; overflow-y: auto; padding: 12px; }
      .kb-cmt-card {
        background: white; border-radius: 10px; padding: 12px 14px;
        margin-bottom: 10px; border-left: 3px solid #6366f1;
        box-shadow: 0 1px 3px rgba(0,0,0,0.07);
      }
      .kb-cmt-quote {
        font-size: 11px; color: #888; font-style: italic;
        background: #fef9c3; border-left: 2px solid #fbbf24;
        padding: 4px 8px; border-radius: 0 4px 4px 0;
        margin-bottom: 7px; line-height: 1.4;
        overflow: hidden; text-overflow: ellipsis;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
      }
      .kb-cmt-text { font-size: 13px; line-height: 1.6; color: #333; margin-bottom: 8px; }
      .kb-cmt-meta { font-size: 11px; color: #bbb; margin-bottom: 8px; }
      .kb-reply {
        border-radius: 8px; padding: 8px 10px; font-size: 12px;
        line-height: 1.6; margin-bottom: 6px; white-space: pre-wrap;
      }
      .kb-reply.ai { background: #f8f7ff; border: 1px solid #ede9fe; color: #444; }
      .kb-reply.user { background: #f0fdf4; border: 1px solid #d1fae5; color: #444; }
      .kb-reply-label { font-size: 10px; color: #9ca3af; margin-bottom: 3px; font-weight: 600; }
      .kb-ai-btn {
        background: #6366f1; color: white; border: none; border-radius: 6px;
        padding: 5px 12px; font-size: 11px; cursor: pointer; margin-top: 4px;
      }
      .kb-ai-btn:hover { background: #4f46e5; }
      .kb-ai-btn:disabled { background: #c7d2fe; cursor: not-allowed; }
      .kb-thinking { font-size: 11px; color: #9ca3af; padding: 4px 0; font-style: italic; }
      #kb-cp-input-area {
        border-top: 1px solid #e8e8e8; padding: 12px; background: white; flex-shrink: 0;
      }
      #kb-cp-quote-preview {
        font-size: 11px; color: #888; font-style: italic;
        background: #fef9c3; border-left: 2px solid #fbbf24;
        padding: 4px 8px; border-radius: 0 4px 4px 0;
        margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      #kb-cp-textarea {
        width: 100%; border: 1px solid #e0e0e0; border-radius: 8px;
        padding: 8px 10px; font-size: 13px; font-family: inherit;
        height: 68px; resize: none; outline: none; display: block;
        background: #fff; color: #333;
      }
      #kb-cp-textarea:focus { border-color: #6366f1; }
      #kb-cp-send-btn {
        margin-top: 8px; background: #333; color: white; border: none;
        border-radius: 6px; padding: 6px 16px; font-size: 12px; cursor: pointer;
      }
      #kb-cp-send-btn:hover { background: #111; }
      #kb-cp-send-status { font-size: 11px; color: #888; margin-left: 8px; }
      .kb-empty { text-align: center; color: #ccc; padding: 40px 0; font-size: 13px; }
    `;
    document.head.appendChild(s);
  }

  // ── 构建面板 ──
  function buildPanel() {
    if (panelEl) return;
    injectStyles();
    panelEl = document.createElement("div");
    panelEl.id = "kb-comment-panel";
    panelEl.innerHTML = `
      <div id="kb-cp-header">
        <h3>💬 评论</h3>
        <button id="kb-cp-close">收起 ›</button>
      </div>
      <div id="kb-cp-body"></div>
      <div id="kb-cp-input-area">
        <div id="kb-cp-quote-preview"></div>
        <textarea id="kb-cp-textarea" placeholder="写评论...（Enter 换行，Cmd+Enter 发送）"></textarea>
        <div style="display:flex;align-items:center;">
          <button id="kb-cp-send-btn">发送</button>
          <span id="kb-cp-send-status"></span>
        </div>
      </div>
    `;
    document.body.appendChild(panelEl);

    document.getElementById("kb-cp-close").addEventListener("click", () => {
      panelEl.classList.add("kb-hidden");
    });
    document.getElementById("kb-cp-send-btn").addEventListener("click", submitComment);
    document.getElementById("kb-cp-textarea").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submitComment();
    });
    // 事件委托：处理评论卡片里的按钮（避免 onclick 属性跨 world 问题）
    document.getElementById("kb-cp-body").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-ask-ai]");
      if (btn) askAI(parseInt(btn.dataset.askAi, 10));
    });
  }

  // ── 渲染评论列表 ──
  function render() {
    const body = document.getElementById("kb-cp-body");
    if (!body) return;
    const comments = load();
    if (!comments.length) {
      body.innerHTML = '<div class="kb-empty">选中文字，右键「💬 评论」添加第一条评论</div>';
      return;
    }
    body.innerHTML = comments.map(c => {
      const t = new Date(c.createdAt);
      const timeStr = `${t.getMonth()+1}/${t.getDate()} ${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`;
      const repliesHtml = c.replies.map(r => `
        <div class="kb-reply ${r.isAI ? "ai" : "user"}">
          <div class="kb-reply-label">${r.isAI ? "🤖 AI" : "你"} · ${new Date(r.createdAt).toLocaleTimeString("zh",{hour:"2-digit",minute:"2-digit"})}</div>
          ${escapeHtml(r.text)}
        </div>
      `).join("");
      const hasAI = c.replies.some(r => r.isAI);
      return `
        <div class="kb-cmt-card" id="kb-cmt-${c.id}">
          ${c.excerpt ? `<div class="kb-cmt-quote">"${escapeHtml(c.excerpt.slice(0,100))}${c.excerpt.length>100?"…":""}"</div>` : ""}
          <div class="kb-cmt-text">${escapeHtml(c.text)}</div>
          <div class="kb-cmt-meta">${timeStr}</div>
          ${repliesHtml}
          ${!hasAI
            ? `<button class="kb-ai-btn" data-ask-ai="${c.id}">✨ 召唤 AI 回复</button>`
            : `<button class="kb-ai-btn" data-ask-ai="${c.id}" style="background:#e0e7ff;color:#4f46e5;">🔄 再次召唤</button>`
          }
        </div>
      `;
    }).join("");
  }

  // ── 提交评论 ──
  async function submitComment() {
    const ta = document.getElementById("kb-cp-textarea");
    const status = document.getElementById("kb-cp-send-status");
    const text = ta.value.trim();
    if (!text) return;
    const btn = document.getElementById("kb-cp-send-btn");
    btn.disabled = true;
    status.textContent = "保存中...";
    addComment(currentExcerpt, text);
    ta.value = "";
    status.textContent = "✓ 已保存";
    setTimeout(() => { status.textContent = ""; }, 2000);
    btn.disabled = false;
    render();
  }

  // ── 回写 Notion ──
  function saveCommentToNotion(comment, aiReply) {
    const platform = (() => {
      const h = location.hostname;
      if (h.includes('localhost')) return '知识库';
      if (h.includes('mp.weixin.qq.com')) return '公众号';
      if (h.includes('substack.com')) return '博客';
      if (h.includes('zhihu.com')) return '知乎';
      if (h.includes('twitter.com') || h.includes('x.com')) return 'Twitter';
      return '网页';
    })();
    const title = `[评论] ${document.title.slice(0, 60)}`;
    chrome.runtime.sendMessage({
      type: "SAVE_TO_NOTION",
      data: {
        title,
        url: location.href,
        platform,
        excerpt: comment.excerpt || "",
        thought: comment.text,
        aiConversation: aiReply ? `AI: ${aiReply}` : ""
      }
    }, (resp) => {
      if (chrome.runtime.lastError) return; // 静默失败，不影响用户
      if (resp && !resp.success) console.warn("[KB] Notion 写入失败:", resp.error);
    });
  }

  // ── AI 回复 ──
  async function askAI(commentId) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return;
    const card = document.getElementById("kb-cmt-" + commentId);
    const btn = card ? card.querySelector(".kb-ai-btn") : null;
    if (btn) { btn.disabled = true; btn.textContent = "AI 思考中..."; }
    // 在卡片里加 thinking 提示
    const thinkingEl = document.createElement("div");
    thinkingEl.className = "kb-thinking";
    thinkingEl.textContent = "AI 正在回复...";
    if (card) card.appendChild(thinkingEl);

    const systemPrompt = `你是用户的数字助手，非常了解这个项目背景：
用户是独立创业者，产品方向是「一人公司 AI 操作系统」，用 AI 虚拟同事替代早期团队。
目标用户：决策型知识工作者。

用户在阅读网页内容时对某段文字发表了评论，请基于以下信息给出有深度的回应：
页面：${location.href}
用户划线内容：「${c.excerpt || "（无）"}」
用户评论：${c.text}

要求：
- 直接回应用户的观点，有话直说
- 结合项目背景给出对创业方向有价值的判断
- 中文回答，不超过300字`;

    try {
      const reply = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type: "CALL_AI", data: { systemPrompt, messages: [{ role: "user", content: c.text }] } },
          (resp) => {
            if (resp && resp.success) resolve(resp.reply);
            else reject(new Error(resp?.error || "AI 调用失败"));
          }
        );
      });
      addReply(commentId, reply, true);
      localStorage.setItem('__kb_last_ai_reply', JSON.stringify({ commentId, reply, ok: true, ts: Date.now() }));
      // 回写 Notion：评论 + AI 回复一起存
      saveCommentToNotion(c, reply);
    } catch (err) {
      const errMsg = "AI 回复失败：" + err.message;
      addReply(commentId, errMsg, true);
      localStorage.setItem('__kb_last_ai_reply', JSON.stringify({ commentId, reply: errMsg, ok: false, ts: Date.now() }));
    }
    if (thinkingEl.parentNode) thinkingEl.remove();
    render();
  }

  // ── 对外接口（由右键菜单触发，先高亮再打开面板）──
  function open(excerpt, url, title) {
    currentExcerpt = excerpt;
    highlightSelection(excerpt);  // 此时 selection 还在，安全
    buildPanel();
    // 更新输入区 quote 预览
    const qp = document.getElementById("kb-cp-quote-preview");
    if (qp) {
      qp.textContent = `"${excerpt.slice(0, 80)}${excerpt.length > 80 ? "…" : ""}"`;
      qp.style.display = "block";
    }
    // 清空输入框，聚焦
    const ta = document.getElementById("kb-cp-textarea");
    if (ta) { ta.value = ""; ta.focus(); }
    panelEl.classList.remove("kb-hidden");
    render();
  }

  // 页面加载时自动渲染已有评论
  function init() {
    const comments = load();
    if (comments.length > 0) {
      buildPanel();
      panelEl.classList.add("kb-hidden"); // 有历史评论但不自动弹出
      render();
      // 显示一个悬浮按钮提示有历史评论
      const badge = document.createElement("button");
      badge.id = "kb-comment-badge";
      badge.textContent = `💬 ${comments.length}`;
      badge.style.cssText = `
        position: fixed; bottom: 24px; right: 24px; z-index: 2147483646;
        background: #6366f1; color: white; border: none; border-radius: 20px;
        padding: 8px 14px; font-size: 13px; cursor: pointer;
        box-shadow: 0 4px 12px rgba(99,102,241,0.4);
        font-family: -apple-system, sans-serif;
      `;
      badge.addEventListener("click", () => {
        if (!panelEl) buildPanel();
        panelEl.classList.remove("kb-hidden");
        badge.remove();
      });
      document.body.appendChild(badge);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
  if (document.readyState !== "loading") init();

  // 测试桥：Playwright 在 main world 无法直接访问 content script 的 window，
  // 通过 postMessage 桥接
  window.addEventListener('message', (e) => {
    if (e.data && e.data.__kb_test === 'open_comment') {
      open(e.data.excerpt, e.data.url, e.data.title);
    }
  });

  return { open, render, load };
})();
