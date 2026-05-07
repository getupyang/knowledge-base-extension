const KB_CONTENT_VERSION = "0.3.1-ai-unread-notice";
console.info(`[KB] content script loaded: ${KB_CONTENT_VERSION}`);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "ADD_COMMENT") {
    // 右键菜单触发：selection 已消失，先用文字匹配高亮，再打开评论面板
    commentSystem.highlightByText(msg.excerpt);
    commentSystem.open(msg.excerpt, msg.url, msg.title);
    sendResponse({ ok: true });
    return;
  }
  if (msg.type === "HIGHLIGHT_AND_SAVE") {
    // 右键菜单触发高亮：此时 selection 已消失，用文字内容匹配恢复
    commentSystem.highlightByText(msg.excerpt);
    commentSystem.saveHighlightToNotion(msg.excerpt, msg.title, msg.url, msg.platform);
    sendResponse({ ok: true });
    return;
  }
});

// ─── 旧聊天侧边栏已移除（由评论系统替代）────────────────────
// REMOVED: openChatPanel, updatePanelContext, sendMessage, callAI,
//          appendMessage, saveConversation, showInputDialog

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

// ─── 前后文截取：获取划线文本前后各200字，解决指代不清 ───
// 优先用 _lastSelectionSurrounding（从 Range 精确获取），fallback 到 indexOf
let _lastSelectionSurrounding = "";
let _iframeHinted = false; // 受限iframe提示只弹一次

function captureSurroundingFromRange(range, chars = 200) {
  // 从 Range 所在的容器节点取 textContent，然后定位 selection 在其中的位置
  try {
    // 往上找一个足够大的容器（段落或 section 级别），避免只拿到一个 span
    let container = range.commonAncestorContainer;
    if (container.nodeType === Node.TEXT_NODE) container = container.parentNode;
    // 往上走最多5层找到足够大的文本块
    for (let i = 0; i < 5 && container.parentNode && container.textContent.length < chars * 2; i++) {
      container = container.parentNode;
      if (container === document.body) break;
    }
    const fullText = container.textContent || "";
    const selText = range.toString();
    const idx = fullText.indexOf(selText);
    if (idx === -1) return fullText.slice(0, chars * 2); // fallback：返回容器前400字
    const start = Math.max(0, idx - chars);
    const end = Math.min(fullText.length, idx + selText.length + chars);
    return fullText.slice(start, end);
  } catch {
    return "";
  }
}

function getSurroundingText(excerpt, chars = 200) {
  // 优先用划线时捕获的精确前后文
  if (_lastSelectionSurrounding) {
    const result = _lastSelectionSurrounding;
    _lastSelectionSurrounding = ""; // 用完清空
    console.log("[KB] getSurroundingText: 用 Range 精确捕获,", result.length, "字");
    return result;
  }
  // fallback：用 indexOf（可能匹配到错误位置）
  if (!excerpt) { console.log("[KB] getSurroundingText: excerpt 为空, 返回空"); return ""; }
  const bodyText = document.body.innerText || "";
  const idx = bodyText.indexOf(excerpt);
  if (idx === -1) { console.log("[KB] getSurroundingText: indexOf 未找到, excerpt前30字:", excerpt.slice(0, 30)); return ""; }
  const start = Math.max(0, idx - chars);
  const end = Math.min(bodyText.length, idx + excerpt.length + chars);
  const result = bodyText.slice(start, end);
  console.log("[KB] getSurroundingText: indexOf fallback,", result.length, "字");
  return result;
}

// ─── 页面全文提取（首次评论时调用，按URL缓存到后端）───
const _pageContentSent = new Set(); // 同一页面同一session只传一次
const _pageExposureSent = new Set(); // 只在受控阅读源记录 weak seen，不采集全量浏览历史
function getPageContent(maxChars = 50000) {
  const text = document.body.innerText || "";
  return text.slice(0, maxChars);
}

function shouldAutoCaptureExposure() {
  const host = location.hostname;
  const path = location.pathname || "";
  if (host === "localhost" && location.port === "8765" && path.startsWith("/topics/")) return true;
  if (host === "getupyang.github.io" && path.includes("/ai-builder-daily/reports/")) return true;
  return false;
}

function capturePageExposureIfAllowed() {
  if (!shouldAutoCaptureExposure()) return;
  const url = location.href.split("#")[0];
  if (_pageExposureSent.has(url)) return;
  const text = getPageContent();
  if (!text || text.length < 300) return;
  _pageExposureSent.add(url);
  fetch("http://localhost:8766/exposures/seen", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      page_url: url,
      page_title: document.title,
      page_content: text,
      source_type: "seen",
      capture_reason: "allowlisted_reading_source",
    }),
  }).catch(() => {
    _pageExposureSent.delete(url);
  });
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
}

// ─── XPath 工具（高亮持久化用）────────────────────────────────

function getXPath(node) {
  if (node.nodeType === Node.TEXT_NODE) node = node.parentNode;
  if (!node || node === document.body) return "/html/body";
  const parts = [];
  while (node && node !== document.body) {
    let idx = 1;
    let sib = node.previousSibling;
    while (sib) { if (sib.nodeType === Node.ELEMENT_NODE && sib.nodeName === node.nodeName) idx++; sib = sib.previousSibling; }
    parts.unshift(`${node.nodeName.toLowerCase()}[${idx}]`);
    node = node.parentNode;
  }
  return "/html/body/" + parts.join("/");
}

function resolveXPath(xpath) {
  try {
    return document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  } catch { return null; }
}

// ─── 小 bar（划线后出现的快捷操作栏）────────────────────────

const selectionBar = (() => {
  let barEl = null;
  let hideTimer = null;
  let savedExcerpt = "";
  let savedRange = null;

  function injectStyles() {
    if (document.getElementById("kb-bar-style")) return;
    const s = document.createElement("style");
    s.id = "kb-bar-style";
    s.textContent = `
      #kb-sel-bar {
        position: absolute;
        z-index: 2147483647;
        display: flex;
        gap: 2px;
        background: oklch(0.18 0.01 60);
        border-radius: 4px;
        padding: 4px 5px;
        box-shadow: 0 6px 20px rgba(40,30,20,0.22);
        font-family: "Inter Tight", "Inter", -apple-system, sans-serif;
        pointer-events: all;
        white-space: nowrap;
        transition: opacity 0.15s;
      }
      #kb-sel-bar button {
        background: none;
        border: none;
        color: oklch(0.985 0.008 75);
        font-size: 12px;
        padding: 4px 10px;
        border-radius: 3px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
        transition: background 0.12s;
        letter-spacing: 0.04em;
      }
      #kb-sel-bar button:hover { background: oklch(0.32 0.01 60); }
      #kb-sel-bar .kb-bar-divider {
        width: 1px; background: oklch(0.32 0.01 60);
        margin: 3px 1px; border-radius: 1px;
      }
    `;
    document.head.appendChild(s);
  }

  function show(rect, excerpt, range) {
    injectStyles();
    hide();
    savedExcerpt = excerpt;
    // 克隆 range 以免 selection 清除后失效
    savedRange = range ? range.cloneRange() : null;

    barEl = document.createElement("div");
    barEl.id = "kb-sel-bar";
    barEl.innerHTML = `
      <button id="kb-bar-highlight" title="仅保存高亮">高亮</button>
      <div class="kb-bar-divider"></div>
      <button id="kb-bar-comment" title="高亮并评注">评注</button>
    `;

    document.body.appendChild(barEl);

    // 定位到选区下方
    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;
    const barW = 160; // 估算宽度，实际渲染后会自适应
    let left = rect.left + scrollX + rect.width / 2 - barW / 2;
    let top = rect.bottom + scrollY + 8;
    if (left < 8) left = 8;
    barEl.style.left = left + "px";
    barEl.style.top = top + "px";

    barEl.querySelector("#kb-bar-highlight").addEventListener("click", (e) => {
      e.stopPropagation();
      hide();
      if (savedRange) _lastSelectionSurrounding = captureSurroundingFromRange(savedRange);
      commentSystem.doHighlight(savedExcerpt, savedRange);
      commentSystem.saveHighlightToNotion(savedExcerpt, document.title, location.href, null);
    });
    barEl.querySelector("#kb-bar-comment").addEventListener("click", (e) => {
      e.stopPropagation();
      hide();
      // 划线时用 Range 精确捕获前后文（在 Range 还有效的时候）
      if (savedRange) _lastSelectionSurrounding = captureSurroundingFromRange(savedRange);
      commentSystem.doHighlightAndOpenComment(savedExcerpt, savedRange);
    });

    // 3秒无操作自动消失
    hideTimer = setTimeout(hide, 3000);
    barEl.addEventListener("mouseenter", () => { clearTimeout(hideTimer); });
    barEl.addEventListener("mouseleave", () => { hideTimer = setTimeout(hide, 1500); });
  }

  function hide() {
    clearTimeout(hideTimer);
    if (barEl) { barEl.remove(); barEl = null; }
  }

  // mouseup 监听：有选中文字时显示 bar
  // 用 capture:true 在捕获阶段触发，避免 SPA（ChatGPT 等）在冒泡阶段 stopPropagation 导致事件丢失
  document.addEventListener("mouseup", (e) => {
    // 如果点击在 bar 自身上，不处理
    if (barEl && barEl.contains(e.target)) return;
    // 如果点击在评论面板上，不处理
    if (e.target.closest("#kb-comment-panel")) return;

    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel?.toString().trim() || "";
      if (text.length < 3) { hide(); return; }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      show(rect, text, range);
    }, 200); // 200ms 延迟，避免与页面自带菜单冲突

  }, true);

  // 点击页面其他地方收起 bar
  document.addEventListener("mousedown", (e) => {
    if (barEl && !barEl.contains(e.target)) hide();
  }, true);

  return { hide };
})();

// ─── 评论系统 ────────────────────────────────────────────────

const DEBUG_MODE = true; // 发布时改为 false

const commentSystem = (() => {
  const STORAGE_KEY = () => "kb_comments_" + location.href.split("?")[0];
  let panelEl = null;
  let currentExcerpt = "";
  const _aiUnreadCommentIds = new Set();

  // ── 提前保存 selection（提交时 selection 已消失，需要提前捕获）──
  let _savedSelection = "";
  document.addEventListener("mouseup", () => {
    const sel = window.getSelection()?.toString().trim() || "";
    if (sel.length > 5) _savedSelection = sel;
  });

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
    setTimeout(updateBadge, 0);
    return c;
  }
  function addReply(commentId, replyText, isAI, debugMeta = null) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return;
    c.replies.push({ id: Date.now(), text: replyText, isAI, debugMeta, createdAt: new Date().toISOString() });
    save(comments);
    return c;
  }

  // ── highlights 持久化存储（独立于 comments）──
  const HL_KEY = () => "kb_highlights_" + location.href.split("?")[0];
  function loadHighlights() {
    try { return JSON.parse(localStorage.getItem(HL_KEY()) || "[]"); } catch { return []; }
  }
  function saveHighlights(hls) {
    localStorage.setItem(HL_KEY(), JSON.stringify(hls));
  }
  function addHighlight(excerpt, position) {
    const hls = loadHighlights();
    const samePosition = (a, b) => JSON.stringify(a || {}) === JSON.stringify(b || {});
    if (hls.some(h => h.excerpt === excerpt && samePosition(h.position, position))) {
      return null;
    }
    const h = { id: Date.now(), excerpt, position, createdAt: new Date().toISOString() };
    hls.unshift(h);
    saveHighlights(hls);
    setTimeout(updateBadge, 0);
    return h;
  }

  // ── 将 range 转为可序列化的 position ──
  function serializeRange(range) {
    try {
      return {
        startXPath: getXPath(range.startContainer),
        startOffset: range.startOffset,
        endXPath: getXPath(range.endContainer),
        endOffset: range.endOffset,
      };
    } catch { return null; }
  }

  // ── 从 position 重建 range ──
  function deserializeRange(pos) {
    try {
      const startNode = resolveXPath(pos.startXPath);
      const endNode = resolveXPath(pos.endXPath);
      if (!startNode || !endNode) return null;
      // XPath 解析到元素节点时，取其对应的文本子节点
      const startText = startNode.nodeType === Node.TEXT_NODE ? startNode : startNode.childNodes[pos.startOffset] || startNode.firstChild;
      const endText = endNode.nodeType === Node.TEXT_NODE ? endNode : endNode.childNodes[pos.endOffset] || endNode.firstChild;
      if (!startText || !endText) return null;
      const r = document.createRange();
      r.setStart(startNode.nodeType === Node.TEXT_NODE ? startNode : startNode, pos.startOffset);
      r.setEnd(endNode.nodeType === Node.TEXT_NODE ? endNode : endNode, pos.endOffset);
      return r;
    } catch { return null; }
  }

  // ── 划线 ↔ 卡片：点击锚点 + hover 联动 ──
  // PDF 第 1 页：hover 划线 → 右侧对应卡片高亮，且因为可能错位，需要锚点动效把卡片滚到视觉中心
  // 反向：hover 卡片 → 左侧对应划线脉冲

  // hover excerpt → 卡片 anchor 高亮（不滚动，避免 hover 误触）
  function _anchorCardForExcerpt(excerpt, opts = {}) {
    const comments = load();
    const match = comments.find(c => c.excerpt === excerpt);
    if (!match) return null;
    const card = document.getElementById("kb-cmt-" + match.id);
    if (!card) return null;
    if (opts.scroll) {
      // 锚点滚动：把卡片滚到面板可视区中央
      card.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (opts.flash) {
      card.classList.remove("kb-flash");
      void card.offsetWidth;
      card.classList.add("kb-flash");
      setTimeout(() => card.classList.remove("kb-flash"), 700);
    }
    if (opts.anchor) {
      card.classList.add("kb-anchor");
    }
    return card;
  }
  function _unanchorAllCards() {
    document.querySelectorAll(".kb-cmt-card.kb-anchor").forEach(el => el.classList.remove("kb-anchor"));
  }

  // 给同组 mark 一起加/去 active class，让多段 mark 视觉上像一整段
  function _setMarkGroupActive(excerpt, active) {
    const id = _excerptId(excerpt);
    document.querySelectorAll('mark.kb-comment-highlight[data-excerpt-id="' + id + '"]').forEach(m => {
      if (active) m.classList.add("kb-mark-active");
      else m.classList.remove("kb-mark-active");
    });
  }

  function _findMarksForExcerpt(excerpt) {
    const id = _excerptId(excerpt);
    return Array.from(document.querySelectorAll('mark.kb-comment-highlight[data-excerpt-id="' + id + '"]'));
  }

  function _bindMarkInteractions(mark, excerpt) {
    // hover mark：整组同 excerpt 的 mark 一起变深 + 右侧对应卡片浮起锚点 + 滚到视区中央
    mark.addEventListener("mouseenter", () => {
      _setMarkGroupActive(excerpt, true);
      if (panelOpen) _anchorCardForExcerpt(excerpt, { anchor: true, scroll: true });
    });
    mark.addEventListener("mouseleave", () => {
      _setMarkGroupActive(excerpt, false);
      _unanchorAllCards();
    });
    // 点击：打开面板 + 锚定 + 闪一下
    mark.addEventListener("click", (e) => {
      e.stopPropagation();
      currentExcerpt = excerpt;
      if (!panelEl) buildPanel();
      const wasPanelClosed = !panelOpen;
      if (!panelOpen) {
        panelOpen = true;
        panelEl.classList.remove("kb-btn-hidden");
        document.body.style.marginRight = "360px";
        updateBadge();
      }
      render();
      const flashDelay = wasPanelClosed ? 350 : 80;
      setTimeout(() => _anchorCardForExcerpt(excerpt, { scroll: true, flash: true }), flashDelay);
    });
  }

  // 用 excerpt 文本生成稳定 hash，作为 mark 的 data-excerpt-id，让多段 mark 视觉上联动成一整体
  function _excerptId(excerpt) {
    let h = 0;
    const s = (excerpt || "").trim();
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return "ex" + (h >>> 0).toString(36);
  }

  function _createMark(excerpt) {
    const mark = document.createElement("mark");
    mark.className = "kb-comment-highlight";
    mark.dataset.excerptId = _excerptId(excerpt);
    // 兜底底色：即使 stylesheet 还没注入也能看到划线（页面 CSS 可能 reset mark 样式）
    // hover/active 由 stylesheet 接管（class 切换覆盖 inline）
    mark.style.background = "oklch(0.94 0.04 35)";
    mark.style.borderRadius = "2px";
    mark.style.cursor = "pointer";
    mark.title = "点击查看评注 / hover 在右栏定位";
    _bindMarkInteractions(mark, excerpt);
    return mark;
  }

  // 检测 range 是否跨越表格单元格
  function _rangeSpansTableCells(range) {
    const ancestor = range.commonAncestorContainer;
    const el = ancestor.nodeType === Node.TEXT_NODE ? ancestor.parentNode : ancestor;
    // 如果公共祖先是 tr/tbody/table，说明跨了 td
    return el && (el.tagName === "TR" || el.tagName === "TBODY" || el.tagName === "TABLE" || el.tagName === "THEAD");
  }

  // 跨表格单元格高亮：逐个 td 内的文字节点分别包 mark，不破坏表格结构
  function _highlightAcrossTableCells(range, excerpt) {
    const marks = [];
    // 收集 range 内所有文字节点
    const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let node;
    while ((node = walker.nextNode())) {
      if (range.intersectsNode(node) && node.textContent.trim()) {
        textNodes.push(node);
      }
    }
    if (textNodes.length === 0) return false;
    for (const tn of textNodes) {
      try {
        const nodeRange = document.createRange();
        // 第一个节点可能只选了部分
        if (tn === range.startContainer) {
          nodeRange.setStart(tn, range.startOffset);
        } else {
          nodeRange.setStart(tn, 0);
        }
        // 最后一个节点可能只选了部分
        if (tn === range.endContainer) {
          nodeRange.setEnd(tn, range.endOffset);
        } else {
          nodeRange.setEnd(tn, tn.length);
        }
        if (nodeRange.toString().trim()) {
          const mark = _createMark(excerpt);
          nodeRange.surroundContents(mark);
          marks.push(mark);
        }
      } catch { /* 单个节点包裹失败，跳过 */ }
    }
    return marks.length > 0;
  }

  // 跨节点高亮：逐个文字节点分别包 mark，不破坏 DOM 结构（列表/段落等）
  function _highlightAcrossNodes(range, excerpt) {
    const marks = [];
    const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let node;
    while ((node = walker.nextNode())) {
      if (range.intersectsNode(node) && node.textContent.trim()) {
        textNodes.push(node);
      }
    }
    if (textNodes.length === 0) return false;
    for (const tn of textNodes) {
      try {
        const nodeRange = document.createRange();
        if (tn === range.startContainer) {
          nodeRange.setStart(tn, range.startOffset);
        } else {
          nodeRange.setStart(tn, 0);
        }
        if (tn === range.endContainer) {
          nodeRange.setEnd(tn, range.endOffset);
        } else {
          nodeRange.setEnd(tn, tn.length);
        }
        if (nodeRange.toString().trim()) {
          const mark = _createMark(excerpt);
          nodeRange.surroundContents(mark);
          marks.push(mark);
        }
      } catch { /* 单个节点包裹失败，跳过 */ }
    }
    return marks.length > 0;
  }

  function insertMark(range, excerpt) {
    // 跨表格单元格时，逐 td 分别高亮，不破坏表格结构
    if (_rangeSpansTableCells(range)) {
      return _highlightAcrossTableCells(range, excerpt);
    }
    const mark = _createMark(excerpt);
    try {
      range.surroundContents(mark);
      // 验证 mark 确实包住了内容
      if (!mark.textContent.trim()) return false;
      return true;
    } catch {
      // 跨节点选区（列表、多段落等）：逐文字节点分别包 mark，不破坏 DOM 结构
      return _highlightAcrossNodes(range, excerpt);
    }
  }

  // ── 高亮（由小bar或右键菜单触发，range 存在）──
  function doHighlight(excerpt, range) {
    if (!range) return;
    const position = serializeRange(range);
    let ok = insertMark(range, excerpt); // 先插 mark，再清 selection
    window.getSelection()?.removeAllRanges();
    // 如果 range 方式失败（跨节点等），fallback 到文字匹配
    if (!ok) {
      highlightByText(excerpt);
      return; // highlightByText 内部已处理 addHighlight
    }
    if (position) {
      addHighlight(excerpt, position);
    }
  }

  // ── 高亮 + 打开评论面板（点"评论"按钮）──
  function doHighlightAndOpenComment(excerpt, range) {
    doHighlight(excerpt, range);
    open(excerpt, location.href, document.title);
  }

  // ── 右键菜单触发：selection 已消失，用文字内容在页面上匹配并高亮 ──
  function highlightByText(excerpt) {
    if (!excerpt) return;
    // 用 TreeWalker 找到文本节点中匹配的位置
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (parent.closest("#kb-comment-panel, #kb-toast, mark.kb-comment-highlight, script, style, textarea")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let node;
    while ((node = walker.nextNode())) {
      const idx = node.textContent.indexOf(excerpt);
      if (idx !== -1) {
        try {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + excerpt.length);
          const position = serializeRange(range);
          insertMark(range, excerpt);
          if (position) addHighlight(excerpt, position);
        } catch { /* 跨节点忽略 */ }
        return;
      }
    }
  }

  // ── 页面加载时恢复所有高亮 ──
  function restoreHighlights() {
    const hls = loadHighlights();
    hls.forEach(h => {
      if (!h.position) return;
      if (_findMarksForExcerpt(h.excerpt).length) return;
      const range = deserializeRange(h.position);
      if (range) insertMark(range, h.excerpt);
    });
  }

  function restoreCommentHighlights() {
    const seen = new Set();
    load().forEach(c => {
      const excerpt = (c.excerpt || "").trim();
      if (!excerpt || seen.has(excerpt)) return;
      seen.add(excerpt);
      if (_findMarksForExcerpt(excerpt).length) return;
      highlightByText(excerpt);
    });
  }

  // ── 高亮后静默保存 Notion（直接走后端代理，不经过 Service Worker）──
  function saveHighlightToNotion(excerpt, title, url, platform) {
    fetch("http://localhost:8766/notion/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: title || document.title,
        url: url || location.href,
        platform: platform || detectLocalPlatform(),
        excerpt,
        thought: "",
        aiConversation: ""
      })
    }).then(r => {
      if (!r.ok) {
        console.error("[KB] Notion save HTTP error:", r.status, r.statusText);
        return r.text().then(t => { throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`); });
      }
      return r.json();
    }).then(resp => {
      if (resp.success) {
        if (resp.notionSynced === false && resp.notionError) console.warn("[KB] Notion not synced:", resp.notionError);
        showToast(resp.notionSynced === false ? "✓ 已高亮并保存到本地（Notion 未同步）" : "✓ 已高亮并保存到 Notion", "success");
      } else {
        const detail = resp.detail || "未知错误";
        console.error("[KB] Notion save failed:", detail);
        showToast("✗ Notion 保存失败：" + detail, "error");
      }
    }).catch(err => {
      console.error("[KB] Notion save error:", err.message || err);
      const msg = err.message || String(err);
      const hint = msg.includes("Failed to fetch")
        ? "本地服务未连接；标注可能只保存在当前页面"
        : msg;
      showToast("✗ 本地保存链路异常：" + hint, "error");
    });
  }

  function detectLocalPlatform() {
    const h = location.hostname;
    if (h.includes('mp.weixin.qq.com')) return '公众号';
    if (h.includes('substack.com')) return '博客';
    if (h.includes('zhihu.com')) return '知乎';
    if (h.includes('twitter.com') || h.includes('x.com')) return 'Twitter';
    if (h.includes('localhost')) return '知识库';
    return '网页';
  }

  // ── badge 常驻（有高亮或评论时显示，点击开关评论栏）──
  let badgeEl = null;
  let panelOpen = false;

  function updateBadge() {
    const comments = load();
    const highlights = loadHighlights();
    const total = comments.length + highlights.filter(h => !comments.find(c => c.excerpt === h.excerpt)).length;
    if (total === 0) {
      if (badgeEl) { badgeEl.remove(); badgeEl = null; }
      return;
    }
    if (!badgeEl) {
      badgeEl = document.createElement("button");
      badgeEl.id = "kb-badge";
      badgeEl.addEventListener("click", togglePanel);
      document.body.appendChild(badgeEl);
    }
    badgeEl.innerHTML = panelOpen
      ? `<span style="font-size:14px">›</span>`
      : `<span style="font-family:'Source Han Serif SC','Noto Serif SC',serif;font-size:13px;letter-spacing:0.04em">评注</span><span style="font-family:'JetBrains Mono',ui-monospace,monospace;font-size:11px;margin-top:3px;opacity:0.85">${total}</span>`;
    badgeEl.style.cssText = `
      position: fixed; right: 0; top: 50%; transform: translateY(-50%);
      z-index: 2147483646; width: 28px; padding: 10px 0;
      background: oklch(0.18 0.01 60); color: oklch(0.985 0.008 75);
      border: none;
      border-radius: 4px 0 0 4px; line-height: 1.3;
      cursor: pointer; display: flex; flex-direction: column; align-items: center;
      box-shadow: -3px 0 12px rgba(40,30,20,0.18);
      font-family: "Inter Tight", "Inter", -apple-system, sans-serif;
      transition: background 0.15s;
    `;
  }

  function togglePanel() {
    if (!panelEl) buildPanel();
    panelOpen = !panelOpen;
    if (panelOpen) {
      panelEl.classList.remove("kb-btn-hidden");
      document.body.style.marginRight = "360px";
      render();
      syncVisibleCommentsFromBackend({ notify: false });
    } else {
      panelEl.classList.add("kb-btn-hidden");
      document.body.style.marginRight = "";
    }
    updateBadge();
  }

  // ── 注入样式（v3 视觉系统：暖纸 + 衬线 + JetBrains Mono + oklch）──
  function injectStyles() {
    if (document.getElementById("kb-comment-style")) return;
    const s = document.createElement("style");
    s.id = "kb-comment-style";
    s.textContent = `
      :root {
        --kb-paper: oklch(0.985 0.008 75);
        --kb-paper-2: oklch(0.97 0.01 75);
        --kb-ink: oklch(0.18 0.01 60);
        --kb-ink-2: oklch(0.32 0.01 60);
        --kb-ink-mute: oklch(0.55 0.02 60);
        --kb-ink-faint: oklch(0.72 0.015 60);
        --kb-line: oklch(0.86 0.01 60);
        --kb-line-2: oklch(0.92 0.008 60);
        --kb-terra: oklch(0.62 0.12 35);
        --kb-terra-soft: oklch(0.93 0.04 35);
        --kb-blue: oklch(0.55 0.12 240);
        --kb-blue-soft: oklch(0.95 0.03 240);
      }
      @keyframes kb-card-flash {
        0%   { box-shadow: 0 1px 2px rgba(60,40,20,0.05); border-color: var(--kb-line); }
        25%  { box-shadow: 0 0 0 3px oklch(0.62 0.12 35 / 0.18); border-color: var(--kb-terra); }
        100% { box-shadow: 0 1px 2px rgba(60,40,20,0.05); border-color: var(--kb-line); }
      }
      mark.kb-comment-highlight {
        background: oklch(0.94 0.04 35) !important;
        color: inherit;
        border-radius: 2px;
        cursor: pointer;
        padding: 1px 1px;
        transition: background 0.2s;
        box-decoration-break: clone;
        -webkit-box-decoration-break: clone;
      }
      mark.kb-comment-highlight.kb-mark-active {
        background: oklch(0.86 0.10 35) !important;
      }
      @keyframes kb-mark-pulse-anim {
        0%   { background: oklch(0.94 0.04 35) !important; box-shadow: 0 0 0 0 oklch(0.62 0.12 35 / 0.0); }
        30%  { background: oklch(0.78 0.14 35) !important; box-shadow: 0 0 0 4px oklch(0.62 0.12 35 / 0.35); }
        100% { background: oklch(0.94 0.04 35) !important; box-shadow: 0 0 0 0 oklch(0.62 0.12 35 / 0.0); }
      }
      mark.kb-comment-highlight.kb-mark-pulse {
        animation: kb-mark-pulse-anim 0.85s ease-in-out 1;
      }
      .kb-cmt-card.kb-flash {
        animation: kb-card-flash 0.7s ease-in-out 1;
      }
      .kb-cmt-card.kb-anchor {
        border-color: var(--kb-terra) !important;
        box-shadow: 0 0 0 3px oklch(0.62 0.12 35 / 0.14) !important;
      }
      #kb-comment-panel {
        position: fixed; top: 0; right: 0; width: 360px; height: 100vh;
        background: var(--kb-paper);
        border-left: 1px solid var(--kb-line);
        display: flex; flex-direction: column; z-index: 2147483645;
        font-family: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
        font-size: 14px; color: var(--kb-ink);
        box-shadow: -8px 0 28px rgba(60,40,20,0.06);
        transform: translateX(0); transition: transform 0.28s ease;
        overflow: hidden;
        background-image:
          radial-gradient(oklch(0.92 0.012 60) 0.5px, transparent 0.5px);
        background-size: 20px 20px;
      }
      #kb-comment-panel.kb-btn-hidden { transform: translateX(100%); }
      #kb-cp-header {
        padding: 14px 16px 12px; border-bottom: 1px solid var(--kb-line);
        display: flex; align-items: baseline; justify-content: space-between;
        background: var(--kb-paper); flex-shrink: 0;
      }
      #kb-cp-header h3 {
        font-size: 16px; font-weight: 500; margin: 0;
        font-family: "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
        color: var(--kb-ink); letter-spacing: 0.02em;
      }
      #kb-cp-header .kb-cp-count {
        font-family: "JetBrains Mono", ui-monospace, monospace;
        font-size: 11px; color: var(--kb-ink-mute);
        margin-left: 6px; font-weight: 400;
      }
      #kb-cp-close {
        background: none; border: none; padding: 4px 8px;
        font-size: 12px; cursor: pointer; color: var(--kb-ink-mute);
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-close:hover { color: var(--kb-terra); }
      #kb-cp-body {
        flex: 1; overflow-y: auto; padding: 12px 14px;
        position: relative;
      }
      .kb-cmt-card {
        background: var(--kb-paper);
        border: 1px solid var(--kb-line-2);
        border-radius: 6px;
        padding: 12px 14px 10px;
        box-shadow: 0 1px 2px rgba(60,40,20,0.04);
        margin-bottom: 10px;
        transition: border-color 0.18s, box-shadow 0.18s;
      }
      .kb-cmt-card:hover { border-color: var(--kb-line); }
      .kb-cmt-card.kb-ai-unread {
        border-color: oklch(0.62 0.12 240);
        box-shadow: 0 0 0 2px oklch(0.62 0.12 240 / 0.10);
      }
      .kb-cmt-content {
        max-height: 320px; overflow: hidden; position: relative;
        transition: max-height 0.3s ease;
      }
      .kb-cmt-content.expanded { max-height: none; overflow: visible; }
      .kb-cmt-content.overflowing:not(.expanded)::after {
        content: ''; position: absolute; bottom: 0; left: 0; right: 0;
        height: 36px;
        background: linear-gradient(transparent, var(--kb-paper));
      }
      .kb-cmt-expand {
        font-size: 11px; color: var(--kb-terra); cursor: pointer; margin-top: 4px;
        background: none; border: none; padding: 0; text-align: left;
        font-family: inherit; letter-spacing: 0.03em;
      }
      .kb-cmt-expand:hover { text-decoration: underline; }
      .kb-cmt-quote {
        font-size: 12px; color: var(--kb-ink-2);
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
        font-style: italic;
        border-left: 2px solid var(--kb-terra);
        padding: 2px 0 2px 10px;
        margin-bottom: 8px; line-height: 1.5;
        overflow: hidden;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
      }
      .kb-cmt-text {
        font-size: 14px; line-height: 1.65; color: var(--kb-ink);
        margin-bottom: 6px;
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
      }
      .kb-cmt-meta {
        font-size: 10px; color: var(--kb-ink-faint);
        font-family: "JetBrains Mono", ui-monospace, monospace;
        margin-bottom: 8px; letter-spacing: 0.04em;
      }
      .kb-reply {
        border-radius: 4px;
        padding: 8px 10px; font-size: 13px;
        line-height: 1.55; margin-bottom: 6px;
      }
      .kb-reply.ai {
        background: var(--kb-paper-2);
        border-left: 2px solid var(--kb-blue);
        color: var(--kb-ink);
      }
      .kb-reply.user {
        background: oklch(0.97 0.015 130);
        border-left: 2px solid oklch(0.65 0.1 130);
        color: var(--kb-ink);
      }
      .kb-reply-label {
        font-size: 10px; color: var(--kb-ink-mute); margin-bottom: 4px;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        font-weight: 500; letter-spacing: 0.06em;
      }
      .kb-reply-label .kb-reply-tag {
        display: inline-block;
        background: var(--kb-ink); color: var(--kb-paper);
        font-size: 9px; padding: 1px 5px; border-radius: 2px;
        margin-right: 6px; letter-spacing: 0.08em; font-weight: 600;
      }
      .kb-reply-body { font-size: 13px; line-height: 1.6; margin: 0; }
      .kb-reply.ai .kb-reply-body > p:first-child { margin-top: 0; }
      .kb-reply.ai .kb-reply-body p { margin: 0 0 4px 0; }
      .kb-reply.ai .kb-reply-body p:last-child { margin-bottom: 0; }
      .kb-reply.user .kb-reply-body { white-space: pre-wrap; }
      .kb-reply.ai .kb-reply-body ul, .kb-reply.ai .kb-reply-body ol { margin: 3px 0 5px 0; padding-left: 16px; }
      .kb-reply.ai .kb-reply-body li { margin-bottom: 2px; }
      .kb-reply.ai .kb-reply-body h1, .kb-reply.ai .kb-reply-body h2 { font-size: 13px; font-weight: 600; margin: 6px 0 3px; }
      .kb-reply.ai .kb-reply-body h3, .kb-reply.ai .kb-reply-body h4 { font-size: 13px; font-weight: 600; margin: 5px 0 3px; }
      .kb-reply.ai .kb-reply-body strong { font-weight: 600; }
      .kb-reply.ai .kb-reply-body em { font-style: italic; }
      .kb-reply.ai .kb-reply-body code {
        font-family: "JetBrains Mono", ui-monospace, monospace;
        background: var(--kb-paper); border: 1px solid var(--kb-line-2);
        padding: 1px 4px; border-radius: 2px; font-size: 11px;
      }
      .kb-reply.ai .kb-reply-body pre {
        background: var(--kb-paper); border: 1px solid var(--kb-line-2);
        padding: 8px 10px; border-radius: 4px; overflow-x: auto; margin: 5px 0;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      .kb-reply.ai .kb-reply-body table { border-collapse: collapse; width: 100%; font-size: 11px; margin: 5px 0; }
      .kb-reply.ai .kb-reply-body th, .kb-reply.ai .kb-reply-body td { border: 1px solid var(--kb-line); padding: 3px 6px; text-align: left; }
      .kb-reply.ai .kb-reply-body th { background: var(--kb-paper-2); font-weight: 600; }
      .kb-reply.ai .kb-reply-body blockquote { border-left: 2px solid var(--kb-line); padding-left: 8px; margin: 4px 0; color: var(--kb-ink-mute); }
      .kb-reply.ai .kb-reply-body a { color: var(--kb-blue); text-decoration: none; border-bottom: 1px solid oklch(0.55 0.12 240 / 0.4); }
      .kb-reply.ai .kb-reply-body a:hover { border-bottom-color: var(--kb-blue); }
      .kb-reply + .kb-reply { margin-top: 5px; }
      .kb-inline-reply { margin-top: 8px; }
      .kb-inline-reply textarea {
        width: 100%; border: 1px solid var(--kb-line); border-radius: 4px;
        padding: 8px 10px; font-size: 13px;
        font-family: "Source Han Serif SC", "Noto Serif SC", "Inter Tight", sans-serif;
        height: 60px; resize: none; outline: none; display: block;
        background: var(--kb-paper); color: var(--kb-ink); box-sizing: border-box;
        line-height: 1.5;
      }
      .kb-inline-reply textarea:focus { border-color: var(--kb-terra); }
      .kb-inline-reply-actions { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
      .kb-reply-send {
        background: var(--kb-ink); color: var(--kb-paper);
        border: none; border-radius: 3px; padding: 5px 12px;
        font-size: 11px; cursor: pointer; letter-spacing: 0.04em;
        font-family: inherit;
      }
      .kb-reply-send:hover { background: oklch(0.28 0.01 60); }
      .kb-reply-btn {
        background: none; border: none;
        color: var(--kb-ink-mute); font-size: 11px;
        cursor: pointer; padding: 0; font-family: inherit;
      }
      .kb-reply-btn:hover { color: var(--kb-terra); }
      .kb-ai-btn {
        background: var(--kb-ink); color: var(--kb-paper);
        border: none; border-radius: 3px;
        padding: 6px 14px; font-size: 11px; cursor: pointer; margin-top: 6px;
        font-family: inherit; letter-spacing: 0.04em;
      }
      .kb-ai-btn:hover { background: oklch(0.28 0.01 60); }
      .kb-ai-btn:disabled { background: var(--kb-ink-faint); cursor: not-allowed; }
      .kb-ai-ready-btn {
        border: 1px solid oklch(0.62 0.12 240 / 0.42);
        background: var(--kb-blue-soft);
        color: var(--kb-blue);
        border-radius: 3px;
        padding: 4px 9px;
        font-size: 11px;
        cursor: pointer;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        letter-spacing: 0.03em;
      }
      .kb-ai-ready-btn:hover {
        border-color: var(--kb-blue);
        background: oklch(0.92 0.04 240);
      }
      .kb-thinking {
        font-size: 12px; color: var(--kb-ink-2); padding: 8px 10px;
        background: var(--kb-paper-2); border-radius: 4px; margin-top: 6px;
        line-height: 1.6; border-left: 2px solid var(--kb-blue);
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
      }
      .kb-thinking::before {
        content: '·'; margin-right: 4px;
        animation: kb-dot 1.4s ease-in-out infinite;
      }
      @keyframes kb-dot { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
      #kb-cp-input-area {
        border-top: 1px solid var(--kb-line); padding: 12px 14px;
        background: var(--kb-paper); flex-shrink: 0;
        max-height: 240px; overflow: hidden;
        transition: max-height 0.22s ease, padding 0.22s ease, opacity 0.18s;
      }
      #kb-cp-input-area.kb-input-collapsed {
        max-height: 0; padding-top: 0; padding-bottom: 0;
        opacity: 0; pointer-events: none;
        border-top-color: transparent;
      }
      #kb-cp-new-btn {
        background: none; border: 1px dashed var(--kb-line);
        color: var(--kb-ink-mute); padding: 4px 10px;
        border-radius: 3px; font-size: 11px; cursor: pointer;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-new-btn:hover { border-color: var(--kb-terra); color: var(--kb-terra); border-style: solid; }
      #kb-cp-new-btn.kb-hidden { display: none; }
      #kb-cp-quote-preview {
        font-size: 11px; color: var(--kb-ink-2);
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
        font-style: italic;
        border-left: 2px solid var(--kb-terra);
        padding: 2px 0 2px 8px;
        margin-bottom: 8px; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
      }
      #kb-cp-textarea {
        width: 100%; border: 1px solid var(--kb-line); border-radius: 4px;
        padding: 10px 12px; font-size: 14px;
        font-family: "Source Han Serif SC", "Noto Serif SC", "Inter Tight", sans-serif;
        height: 76px; resize: none; outline: none; display: block;
        background: var(--kb-paper); color: var(--kb-ink); box-sizing: border-box;
        line-height: 1.55;
      }
      #kb-cp-textarea:focus { border-color: var(--kb-terra); }
      #kb-cp-send-btn {
        margin-top: 8px; background: var(--kb-ink); color: var(--kb-paper);
        border: none; border-radius: 3px;
        padding: 7px 18px; font-size: 12px; cursor: pointer;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-send-btn:hover { background: oklch(0.28 0.01 60); }
      #kb-cp-send-status {
        font-size: 11px; color: var(--kb-ink-mute); margin-left: 10px;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      #kb-cp-ai-notice {
        position: absolute;
        top: 58px;
        left: 12px;
        right: 12px;
        z-index: 3;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 10px;
        border: 1px solid oklch(0.62 0.12 240 / 0.28);
        border-radius: 6px;
        background: oklch(0.98 0.015 240 / 0.96);
        color: var(--kb-ink);
        box-shadow: 0 4px 16px rgba(40, 50, 80, 0.10);
        font-size: 12px;
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
      }
      #kb-cp-ai-notice.kb-hidden { display: none; }
      #kb-cp-ai-notice button {
        border: none;
        background: var(--kb-blue);
        color: white;
        border-radius: 3px;
        padding: 4px 9px;
        font-size: 11px;
        cursor: pointer;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      .kb-empty {
        text-align: center; color: var(--kb-ink-faint);
        padding: 60px 20px; font-size: 13px;
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
        font-style: italic; line-height: 1.7;
      }
      .kb-debug { margin-top: 6px; }
      .kb-debug summary {
        font-size: 10px; color: var(--kb-ink-faint); cursor: pointer;
        user-select: none;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      .kb-debug-body {
        font-size: 10px; color: var(--kb-ink-faint); line-height: 1.8;
        padding: 4px 0 0 8px;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      .kb-expand-hidden { display: none !important; }
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
        <h3>评注<span class="kb-cp-count" id="kb-cp-count"></span></h3>
        <div style="display:flex;gap:8px;align-items:center;">
          <button id="kb-cp-new-btn" class="kb-hidden" title="无划线时手动写一条">+ 新评注</button>
          <button id="kb-cp-close">收起 ›</button>
        </div>
      </div>
      <div id="kb-cp-ai-notice" class="kb-hidden"></div>
      <div id="kb-cp-body"></div>
      <div id="kb-cp-input-area" class="kb-input-collapsed">
        <div id="kb-cp-quote-preview"></div>
        <textarea id="kb-cp-textarea" placeholder="写下你的判断…（Cmd+Enter 发送）"></textarea>
        <div style="display:flex;align-items:center;">
          <button id="kb-cp-send-btn">发送</button>
          <span id="kb-cp-send-status"></span>
        </div>
      </div>
    `;
    document.body.appendChild(panelEl);

    document.getElementById("kb-cp-close").addEventListener("click", () => {
      panelOpen = false;
      panelEl.classList.add("kb-btn-hidden");
      document.body.style.marginRight = "";
      updateBadge();
    });
    document.getElementById("kb-cp-send-btn").addEventListener("click", submitComment);
    document.getElementById("kb-cp-textarea").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submitComment();
      // Esc 收起输入区
      if (e.key === "Escape") collapseInputArea();
    });
    document.getElementById("kb-cp-new-btn").addEventListener("click", () => {
      // 用户手动展开：清空 quote（这是无关联的新评注）
      currentExcerpt = "";
      const qp = document.getElementById("kb-cp-quote-preview");
      if (qp) { qp.textContent = ""; qp.style.display = "none"; }
      expandInputArea(true);
    });
    // 事件委托：处理评论卡片里的按钮（避免 onclick 属性跨 world 问题）
    document.getElementById("kb-cp-body").addEventListener("click", (e) => {
      const readyBtn = e.target.closest("[data-jump-ai]");
      if (readyBtn) {
        jumpToAiReply(parseInt(readyBtn.dataset.jumpAi, 10));
        return;
      }
      const btn = e.target.closest("[data-ask-ai]");
      if (btn) askAI(parseInt(btn.dataset.askAi, 10));
    });
    document.getElementById("kb-cp-ai-notice").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-jump-unread-ai]");
      if (!btn) return;
      const firstId = Array.from(_aiUnreadCommentIds)[0];
      if (firstId) jumpToAiReply(firstId);
    });

    // 反向联动：用 data-excerpt-id 找同组 mark，避免多段 mark 文本拼接问题
    const cpBody = document.getElementById("kb-cp-body");
    // 找 mark 所在的"段落容器"（用于点击卡片时锚定整段，而非 mark 本身）
    function _findParagraphFor(mark) {
      let n = mark.parentElement;
      const blockTags = new Set(["P","LI","BLOCKQUOTE","SECTION","ARTICLE","TD","TH","DIV","H1","H2","H3","H4","H5","H6"]);
      while (n && n !== document.body) {
        if (blockTags.has(n.tagName)) return n;
        n = n.parentElement;
      }
      return mark;
    }

    // hover 卡片 → 左侧整组 mark 一起变深（仅视觉，不滚正文，避免鼠标乱晃跳动）
    cpBody.addEventListener("mouseover", (e) => {
      const card = e.target.closest(".kb-cmt-card");
      if (!card) return;
      const cid = card.id.replace("kb-cmt-", "");
      const c = load().find(x => String(x.id) === cid);
      if (!c || !c.excerpt) return;
      _setMarkGroupActive(c.excerpt, true);
    });
    cpBody.addEventListener("mouseout", (e) => {
      const card = e.target.closest(".kb-cmt-card");
      if (!card) return;
      const related = e.relatedTarget;
      if (related && card.contains(related)) return; // 还在卡片内部移动
      const cid = card.id.replace("kb-cmt-", "");
      const c = load().find(x => String(x.id) === cid);
      if (!c || !c.excerpt) return;
      _setMarkGroupActive(c.excerpt, false);
    });

    // 点击卡片 → 左侧锚定到划线所在段落（不是只看 mark），整组 mark 脉冲
    cpBody.addEventListener("click", (e) => {
      if (e.target.closest("button, textarea, summary, a, [data-expand], [data-open-reply], [data-close-reply], [data-send-reply], [data-ask-ai]")) return;
      const card = e.target.closest(".kb-cmt-card");
      if (!card) return;
      const cid = card.id.replace("kb-cmt-", "");
      const c = load().find(x => String(x.id) === cid);
      if (!c || !c.excerpt) return;
      let marks = _findMarksForExcerpt(c.excerpt);
      if (!marks.length) {
        highlightByText(c.excerpt);
        marks = _findMarksForExcerpt(c.excerpt);
      }
      if (!marks.length) return;
      // 滚到段落容器顶部偏上一点，让上下文一起出现
      const para = _findParagraphFor(marks[0]);
      try { para.scrollIntoView({ behavior: "smooth", block: "center" }); } catch {}
      // 脉冲所有同 excerpt 的 mark
      marks.forEach(m => {
        m.classList.remove("kb-mark-pulse");
        void m.offsetWidth;
        m.classList.add("kb-mark-pulse");
        setTimeout(() => m.classList.remove("kb-mark-pulse"), 900);
      });
    });
  }

  // ── 输入区展开/收起 ──
  function expandInputArea(focusTextarea = true) {
    const area = document.getElementById("kb-cp-input-area");
    const newBtn = document.getElementById("kb-cp-new-btn");
    if (!area) return;
    area.classList.remove("kb-input-collapsed");
    if (newBtn) newBtn.classList.add("kb-hidden");
    if (focusTextarea) {
      const ta = document.getElementById("kb-cp-textarea");
      if (ta) setTimeout(() => ta.focus(), 80);
    }
  }
  function collapseInputArea() {
    const area = document.getElementById("kb-cp-input-area");
    const newBtn = document.getElementById("kb-cp-new-btn");
    const ta = document.getElementById("kb-cp-textarea");
    const qp = document.getElementById("kb-cp-quote-preview");
    if (!area) return;
    area.classList.add("kb-input-collapsed");
    if (newBtn) newBtn.classList.remove("kb-hidden");
    if (ta) ta.value = "";
    if (qp) { qp.textContent = ""; qp.style.display = "none"; }
    currentExcerpt = "";
  }

  function _readCardUiState(commentId) {
    const contentEl = document.getElementById("kb-cmt-content-" + commentId);
    const replyBox = document.getElementById("kb-inline-reply-" + commentId);
    const replyTa = document.getElementById("kb-reply-ta-" + commentId);
    return {
      expanded: !!contentEl?.classList.contains("expanded"),
      replyOpen: !!replyBox && !replyBox.classList.contains("kb-expand-hidden"),
      replyDraft: replyTa?.value || "",
    };
  }

  function _applyCardUiState(commentId, state) {
    const contentEl = document.getElementById("kb-cmt-content-" + commentId);
    const expandBtn = document.getElementById("kb-cmt-expand-" + commentId);
    const replyBox = document.getElementById("kb-inline-reply-" + commentId);
    const replyTa = document.getElementById("kb-reply-ta-" + commentId);
    if (contentEl && state?.expanded) contentEl.classList.add("expanded");
    if (expandBtn && state?.expanded) expandBtn.textContent = "收起 ↑";
    if (replyBox && state?.replyOpen) replyBox.classList.remove("kb-expand-hidden");
    if (replyTa && state?.replyDraft) replyTa.value = state.replyDraft;
  }

  function _refreshCardOverflow(commentId) {
    const contentEl = document.getElementById("kb-cmt-content-" + commentId);
    const expandBtn = document.getElementById("kb-cmt-expand-" + commentId);
    if (!contentEl || !expandBtn) return;
    contentEl.classList.remove("overflowing");
    if (contentEl.scrollHeight > 300 + 10) {
      contentEl.classList.add("overflowing");
      expandBtn.classList.remove("kb-expand-hidden");
    }
  }

  function _getPanelScrollAnchor(body) {
    if (!body) return null;
    const bodyRect = body.getBoundingClientRect();
    const cards = Array.from(body.querySelectorAll(".kb-cmt-card"));
    const card = cards.find(el => {
      const r = el.getBoundingClientRect();
      return r.bottom > bodyRect.top + 8 && r.top < bodyRect.bottom - 8;
    });
    if (!card) return null;
    return {
      id: card.id,
      offsetTop: card.getBoundingClientRect().top - bodyRect.top,
    };
  }

  function _restorePanelScrollAnchor(body, anchor) {
    if (!body || !anchor) return;
    const card = document.getElementById(anchor.id);
    if (!card) return;
    const bodyRect = body.getBoundingClientRect();
    const nextOffsetTop = card.getBoundingClientRect().top - bodyRect.top;
    body.scrollTop += nextOffsetTop - anchor.offsetTop;
  }

  function _isCommentCardVisible(commentId) {
    const body = document.getElementById("kb-cp-body");
    const card = document.getElementById("kb-cmt-" + commentId);
    if (!body || !card || !panelOpen || panelEl?.classList.contains("kb-btn-hidden")) return false;
    const bodyRect = body.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const overlap = Math.min(cardRect.bottom, bodyRect.bottom) - Math.max(cardRect.top, bodyRect.top);
    return overlap > Math.min(140, Math.max(80, cardRect.height * 0.28));
  }

  function _refreshAiNotice() {
    const notice = document.getElementById("kb-cp-ai-notice");
    if (!notice) return;
    const count = _aiUnreadCommentIds.size;
    if (!count) {
      notice.classList.add("kb-hidden");
      notice.innerHTML = "";
      return;
    }
    notice.classList.remove("kb-hidden");
    notice.innerHTML = `
      <span>${count === 1 ? "有 1 条 AI 回复完成" : `有 ${count} 条 AI 回复完成`}</span>
      <button data-jump-unread-ai="1">查看</button>
    `;
  }

  function _markAiReplyReady(commentId) {
    if (_isCommentCardVisible(commentId)) return;
    _aiUnreadCommentIds.add(commentId);
    _refreshAiNotice();
  }

  function _clearAiReplyReady(commentId) {
    if (!_aiUnreadCommentIds.delete(commentId)) return;
    _refreshAiNotice();
    updateCommentCard(commentId);
  }

  const _backendSyncRunning = new Set();

  function _remoteReplyAlreadyLocal(localReplies, remote, isAI, text) {
    return localReplies.some(r => {
      if (r.isAI !== isAI) return false;
      if (r.remoteReplyId && Number(r.remoteReplyId) === Number(remote.id)) return true;
      return (r.text || "").trim() === (text || "").trim();
    });
  }

  async function syncCommentFromBackend(commentId, opts = {}) {
    if (_backendSyncRunning.has(commentId)) return false;
    const localBefore = load().find(x => x.id === commentId);
    if (!localBefore?.agentCommentId) return false;
    _backendSyncRunning.add(commentId);
    try {
      const resp = await fetch(`http://localhost:8766/comments/${localBefore.agentCommentId}`);
      if (!resp.ok) return false;
      const remote = await resp.json();
      const comments = load();
      const c = comments.find(x => x.id === commentId);
      if (!c) return false;
      c.replies = Array.isArray(c.replies) ? c.replies : [];
      let changed = false;
      let addedAI = false;
      for (const rr of remote.replies || []) {
        const text = rr.content || "";
        if (!text.trim()) continue;
        const isAI = rr.author === "agent";
        if (_remoteReplyAlreadyLocal(c.replies, rr, isAI, text)) continue;
        c.replies.push({
          id: Date.now() + c.replies.length,
          remoteReplyId: rr.id,
          text,
          isAI,
          debugMeta: rr.debug_meta || null,
          createdAt: rr.created_at || new Date().toISOString(),
        });
        changed = true;
        if (isAI) addedAI = true;
      }
      if (!changed) return false;
      c.replies.sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      save(comments);
      if (addedAI && opts.notify && !_isCommentCardVisible(commentId)) {
        _markAiReplyReady(commentId);
      }
      updateCommentCard(commentId);
      return true;
    } catch {
      return false;
    } finally {
      _backendSyncRunning.delete(commentId);
    }
  }

  function syncVisibleCommentsFromBackend(opts = {}) {
    const comments = load().filter(c => c.agentCommentId);
    comments.forEach(c => syncCommentFromBackend(c.id, opts));
  }

  function jumpToAiReply(commentId) {
    if (!panelEl) buildPanel();
    if (!panelOpen) {
      panelOpen = true;
      panelEl.classList.remove("kb-btn-hidden");
      document.body.style.marginRight = "360px";
      updateBadge();
    }
    _clearAiReplyReady(commentId);
    const card = document.getElementById("kb-cmt-" + commentId);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("kb-flash");
      setTimeout(() => card.classList.remove("kb-flash"), 900);
    }
  }

  function debugMarkUnreadAiReply() {
    buildPanel();
    if (!panelOpen) {
      panelOpen = true;
      panelEl.classList.remove("kb-btn-hidden");
      document.body.style.marginRight = "360px";
      updateBadge();
    }
    const comments = load();
    if (!comments.length) {
      showToast("先在当前页面留一条评论，再测试 AI 未读提醒", "error");
      return;
    }
    const target = comments.find(c => !_isCommentCardVisible(c.id)) || comments[comments.length - 1];
    _aiUnreadCommentIds.add(target.id);
    updateCommentCard(target.id);
    _refreshAiNotice();
    showToast(`已模拟当前脚本版本：${KB_CONTENT_VERSION}`, "success");
  }

  function _renderCommentCard(c) {
    const t = new Date(c.createdAt);
    const timeStr = `${t.getMonth()+1}/${t.getDate()} ${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`;
    const repliesHtml = c.replies.map(r => {
      let debugHtml = "";
      if (DEBUG_MODE && r.isAI && r.debugMeta) {
        try {
          const dm = typeof r.debugMeta === "string" ? JSON.parse(r.debugMeta) : r.debugMeta;
          if (dm.version) {
            const ver = dm.version === "v1_fallback" ? "v1↓" : "v2";
            const roleLabel = dm.role || "?";
            const intentLabel = dm.intent || "?";
            const quickLabel = dm.is_quick ? " ⚡quick" : "";
            const planLabel = dm.is_plan ? " 📋plan" : "";
            const rulesHtml = (dm.rules_applied || []).length > 0
              ? `<br>Rules: ${dm.rules_applied.map(r => `「${r}」`).join(" ")}`
              : "";
            debugHtml = `
              <details class="kb-debug">
                <summary>▶ Debug</summary>
                <div class="kb-debug-body">
                  [${ver}] ${intentLabel}/${roleLabel}${quickLabel}${planLabel} · ${dm.elapsed_s}s · ~${dm.prompt_tokens_est || "?"} tokens<br>
                  状态: ${dm.status}${rulesHtml}
                </div>
              </details>`;
          } else {
            const ctx = dm.context_layers || {};
            const notionOk = ctx.notion_memory ? "✓" : "✗";
            const selOk = ctx.selected_text ? "✓" : "✗";
            debugHtml = `
              <details class="kb-debug">
                <summary>▶ Debug</summary>
                <div class="kb-debug-body">
                  [v1] @${dm.agent_type} · ${dm.elapsed_s}s · ~${dm.prompt_tokens_est} tokens<br>
                  Context: project✓ notion${notionOk} selected_text${selOk}<br>
                  状态: ${dm.status}
                </div>
              </details>`;
          }
        } catch (e) { /* 解析失败静默 */ }
      }
      const safeText = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const bodyHtml = r.isAI
        ? (typeof marked !== "undefined" ? marked.parse(r.text) : safeText(r.text))
        : `<span>${safeText(r.text)}</span>`;
      const ageS = Math.max(1, Math.round((Date.now() - new Date(r.createdAt).getTime()) / 1000));
      const ageStr = ageS < 60 ? `${ageS} 秒前`
                   : ageS < 3600 ? `${Math.round(ageS/60)} 分钟前`
                   : ageS < 86400 ? `${Math.round(ageS/3600)} 小时前`
                   : new Date(r.createdAt).toLocaleString("zh",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
      const labelHtml = r.isAI
        ? `<span class="kb-reply-tag">AI</span>响应 · ${ageStr}`
        : `你 · ${ageStr}`;
      return `
      <div class="kb-reply ${r.isAI ? "ai" : "user"}">
        <div class="kb-reply-label">${labelHtml}</div>
        <div class="kb-reply-body">${bodyHtml}</div>
        ${debugHtml}
      </div>`;
    }).join("");
    const isFailedAIReply = (r) => r.isAI && /^AI 回复失败/.test(r.text || "");
    const hasSuccessfulAI = c.replies.some(r => r.isAI && !isFailedAIReply(r));
    const hasFailedAIReply = c.replies.some(isFailedAIReply);
    return `
      <div class="kb-cmt-card ${_aiUnreadCommentIds.has(c.id) ? "kb-ai-unread" : ""}" id="kb-cmt-${c.id}">
        ${c.excerpt ? `<div class="kb-cmt-quote">"${escapeHtml(c.excerpt.slice(0,100))}${c.excerpt.length>100?"…":""}"</div>` : ""}
        <div class="kb-cmt-content" id="kb-cmt-content-${c.id}">
          <div class="kb-cmt-text">${escapeHtml(c.text)}</div>
          <div class="kb-cmt-meta">${timeStr}</div>
          ${repliesHtml}
        </div>
        <button class="kb-cmt-expand kb-expand-hidden" id="kb-cmt-expand-${c.id}" data-expand="${c.id}">展开全部 ↓</button>
        <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">
          ${_askAIRunning.has(c.id)
            ? `<span style="color:var(--kb-blue);font-size:11px;font-family:'JetBrains Mono',monospace;letter-spacing:0.04em;">AI 思考中…</span>`
            : _aiUnreadCommentIds.has(c.id)
              ? `<button class="kb-ai-ready-btn" data-jump-ai="${c.id}">AI 已回复 · 查看</button>`
            : !hasSuccessfulAI
              ? `<button class="kb-ai-btn" data-ask-ai="${c.id}">${hasFailedAIReply ? "重新召唤 AI" : "请 AI 回复"}</button>`
              : `<button class="kb-reply-btn" data-open-reply="${c.id}">继续追问</button>`
          }
        </div>
        <div class="kb-inline-reply kb-expand-hidden" id="kb-inline-reply-${c.id}">
          <textarea placeholder="继续追问…（Cmd+Enter 发送）" id="kb-reply-ta-${c.id}"></textarea>
          <div class="kb-inline-reply-actions">
            <button class="kb-reply-send" data-send-reply="${c.id}">发送</button>
            <button class="kb-reply-btn" data-close-reply="${c.id}">取消</button>
          </div>
        </div>
      </div>
    `;
  }

  function updateCommentCard(commentId) {
    const body = document.getElementById("kb-cp-body");
    const oldCard = document.getElementById("kb-cmt-" + commentId);
    if (!body || !oldCard) {
      render();
      return;
    }
    const c = load().find(x => x.id === commentId);
    if (!c) {
      oldCard.remove();
      return;
    }
    const uiState = _readCardUiState(commentId);
    const scrollAnchor = _getPanelScrollAnchor(body);
    const wrapper = document.createElement("div");
    wrapper.innerHTML = _renderCommentCard(c).trim();
    const newCard = wrapper.firstElementChild;
    oldCard.replaceWith(newCard);
    _applyCardUiState(commentId, uiState);
    _refreshCardOverflow(commentId);
    _restorePanelScrollAnchor(body, scrollAnchor);
  }

  // ── 渲染评论列表 ──
  function render() {
    const body = document.getElementById("kb-cp-body");
    if (!body) return;
    // 保存所有追问框的草稿内容和展开状态，render 后恢复
    const drafts = {};
    body.querySelectorAll("textarea[id^='kb-reply-ta-']").forEach(ta => {
      const id = ta.id.replace("kb-reply-ta-", "");
      const box = document.getElementById("kb-inline-reply-" + id);
      const isOpen = box && !box.classList.contains("kb-expand-hidden");
      if (ta.value || isOpen) {
        drafts[id] = { text: ta.value, open: isOpen };
      }
    });
    const comments = load();
    // 更新顶部计数
    const countEl = document.getElementById("kb-cp-count");
    if (countEl) countEl.textContent = comments.length ? ` · ${comments.length} 条` : "";
    if (!comments.length) {
      body.innerHTML = '<div class="kb-empty">选中文字 → 点「评论」<br>留下你的判断、疑问或偏好</div>';
      return;
    }
    body.innerHTML = comments.map(_renderCommentCard).join("");

    // 检查每张卡片是否溢出，显示折叠按钮
    comments.forEach(c => _refreshCardOverflow(c.id));
    _refreshAiNotice();
    // 恢复追问框草稿和展开状态
    for (const [id, draft] of Object.entries(drafts)) {
      const ta = document.getElementById("kb-reply-ta-" + id);
      const box = document.getElementById("kb-inline-reply-" + id);
      if (ta && draft.text) ta.value = draft.text;
      if (box && draft.open) box.classList.remove("kb-expand-hidden");
    }
  }

  // 事件委托：折叠/展开 + 追问输入框
  document.addEventListener("click", (e) => {
    // 折叠/展开
    const expandBtn = e.target.closest("[data-expand]");
    if (expandBtn) {
      const id = expandBtn.dataset.expand;
      const contentEl = document.getElementById("kb-cmt-content-" + id);
      if (!contentEl) return;
      const isExpanded = contentEl.classList.toggle("expanded");
      expandBtn.textContent = isExpanded ? "收起 ↑" : "展开全部 ↓";
      return;
    }
    // 打开追问框
    const openBtn = e.target.closest("[data-open-reply]");
    if (openBtn) {
      const id = openBtn.dataset.openReply;
      const box = document.getElementById("kb-inline-reply-" + id);
      if (box) { box.classList.remove("kb-expand-hidden"); document.getElementById("kb-reply-ta-" + id)?.focus(); }
      return;
    }
    // 关闭追问框
    const closeBtn = e.target.closest("[data-close-reply]");
    if (closeBtn) {
      const id = closeBtn.dataset.closeReply;
      const box = document.getElementById("kb-inline-reply-" + id);
      if (box) box.classList.add("kb-expand-hidden");
      return;
    }
    // 发送追问
    const sendBtn = e.target.closest("[data-send-reply]");
    if (sendBtn) {
      const id = parseInt(sendBtn.dataset.sendReply, 10);
      submitReply(id);
      return;
    }
  });

  // Cmd+Enter 发送追问
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      const ta = e.target.closest("[id^='kb-reply-ta-']");
      if (ta) {
        const id = parseInt(ta.id.replace("kb-reply-ta-", ""), 10);
        submitReply(id);
      }
    }
  });

  // ── 提交追问（用户回复 AI，再触发 AI）──
  async function submitReply(commentId) {
    const ta = document.getElementById("kb-reply-ta-" + commentId);
    if (!ta) return;
    const text = ta.value.trim();
    if (!text) return;
    ta.value = "";
    const box = document.getElementById("kb-inline-reply-" + commentId);
    if (box) box.classList.add("kb-expand-hidden");

    // 存到 localStorage replies（isAI=false）
    addReply(commentId, text, false);
    updateCommentCard(commentId);

    // 同步到 agent_api（追加 user reply）
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return;
    if (c.agentCommentId) {
      try {
        await fetch(`http://localhost:8766/comments/${c.agentCommentId}/reply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text }),
        });
      } catch { /* 离线时静默 */ }
    }

    // Notion 更新（追加对话内容）
    updateNotionPage(c);

    // 立即触发 AI 回复
    askAI(commentId);
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
    const c = addComment(currentExcerpt, text);
    ta.value = "";

    // 同步写入 agent_api（source of truth），仅存储，不触发 agent
    // 用户点"召唤 AI 回复"时才真正触发，@xxx 只是意图标记
    try {
      const excerpt = currentExcerpt || _savedSelection;
      const surrounding = getSurroundingText(excerpt);
      console.log("[KB] submitComment surrounding_text:", surrounding ? `${surrounding.length}字` : "空（bug: 前后文丢失）");
      // 全文只在该URL首次提交时传（同session内去重）
      const url = location.href.split("?")[0];
      let pageContent = "";
      if (!_pageContentSent.has(url)) {
        pageContent = getPageContent();
        _pageContentSent.add(url);
      }
      const resp = await fetch("http://localhost:8766/comments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          page_url: location.href,
          page_title: document.title,
          selected_text: excerpt,
          surrounding_text: surrounding,
          page_content: pageContent,
          comment: text,
          no_agent: true,  // 告知后端不要立即触发 agent
        }),
      });
      if (resp.ok) {
        const data = await resp.json();
        const comments = load();
        const match = comments.find(x => x.id === c.id);
        if (match) { match.agentCommentId = data.id; save(comments); }
        c.agentCommentId = data.id;
        status.textContent = "✓ 已保存";
      } else {
        status.textContent = "✓ 已保存（agent 离线）";
      }
    } catch {
      status.textContent = "✓ 已保存（agent 离线）";
    }

    // 评论提交时立即写一次 Notion，AI 回复完成后再 upsert 同一条
    updateNotionPage(c);

    setTimeout(() => { status.textContent = ""; }, 3000);
    btn.disabled = false;
    // 确保面板可见（用户可能在面板关闭时提交了评论）
    if (panelEl && !panelOpen) {
      panelOpen = true;
      panelEl.classList.remove("kb-btn-hidden");
      document.body.style.marginRight = "360px";
      updateBadge();
    }
    render();
    // 评论已发送 → 收回底部输入区，避免常驻挡视线
    collapseInputArea();
  }

  // ── Notion upsert：每条划线/评论对应一个 Notion page，追加消息而非新建 ──
  // 直接走后端代理，不经过 Service Worker，避免 SW 休眠导致静默失败
  function updateNotionPage(comment) {
    const platform = (() => {
      const h = location.hostname;
      if (h.includes('localhost')) return '知识库';
      if (h.includes('mp.weixin.qq.com')) return '公众号';
      if (h.includes('substack.com')) return '博客';
      if (h.includes('zhihu.com')) return '知乎';
      if (h.includes('twitter.com') || h.includes('x.com')) return 'Twitter';
      return '网页';
    })();
    const hasAIReply = comment.replies.some(r => r.isAI);
    const allMessages = hasAIReply ? [
      `[${new Date(comment.createdAt).toLocaleString("zh")}] 你: ${comment.text}`,
      ...comment.replies.map(r =>
        `[${new Date(r.createdAt).toLocaleString("zh")}] ${r.isAI ? "AI" : "你"}: ${r.text}`
      )
    ].join("\n\n") : "";

    const title = `[评论] ${document.title.slice(0, 60)}`;
    fetch("http://localhost:8766/notion/upsert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        localCommentId: comment.agentCommentId || null,
        notionPageId: comment.notionPageId || null,
        title,
        url: location.href,
        platform,
        excerpt: comment.excerpt || "",
        thought: comment.text,
        aiConversation: allMessages,
      })
    }).then(r => {
      if (!r.ok) {
        console.error("[KB] Notion upsert HTTP error:", r.status, r.statusText);
        return r.text().then(t => { throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`); });
      }
      return r.json();
    }).then(resp => {
      if (resp.success) {
        if (resp.notionSynced === false && resp.notionError) console.warn("[KB] Notion not synced:", resp.notionError);
        if (resp.pageId && !comment.notionPageId) {
          comment.notionPageId = resp.pageId;
          const comments = load();
          const match = comments.find(x => x.id === comment.id);
          if (match) { match.notionPageId = resp.pageId; save(comments); }
        }
        showToast(resp.notionSynced === false ? "✓ 已保存到本地（Notion 未同步）" : "✓ 已保存到 Notion", "success");
      } else {
        const detail = resp.detail || resp.error || "未知错误";
        console.error("[KB] Notion upsert failed:", detail);
        showToast("✗ Notion 保存失败：" + detail, "error");
      }
    }).catch(err => {
      console.error("[KB] Notion upsert error:", err.message || err);
      const msg = err.message || String(err);
      const hint = msg.includes("Failed to fetch")
        ? "本地服务未连接；评论可能只保存在当前页面"
        : msg;
      showToast("✗ 本地保存链路异常：" + hint, "error");
    });
  }

  // ── 追问输入锁定（AI 回复中禁用，回复完成后解锁）──
  function _lockReplyInput(commentId, locked) {
    const box = document.getElementById("kb-inline-reply-" + commentId);
    if (!box) return;
    const ta = box.querySelector("textarea");
    const sendBtn = box.querySelector("[data-send-reply]");
    if (ta) { ta.disabled = locked; ta.placeholder = locked ? "AI 回复中，请稍候..." : "追问 AI（Cmd+Enter 发送）..."; }
    if (sendBtn) { sendBtn.disabled = locked; sendBtn.textContent = locked ? "AI 回复中..." : "发送 + 召唤 AI"; }
  }

  // ── AI 回复（via agent_api localhost:8766）──
  const _askAIRunning = new Set(); // per-comment 锁，不同评论可以并行 // AI 回复中的 commentId，用于禁用追问输入
  async function askAI(commentId) {
    if (_askAIRunning.has(commentId)) return; // AI 还在回复，忽略
    _askAIRunning.add(commentId);
    updateCommentCard(commentId);
    // 立即禁用追问输入（不等 render，直接操作 DOM）
    _lockReplyInput(commentId, true);

    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) { _askAIRunning.delete(commentId); _lockReplyInput(commentId, false); return; }

    // thinkingEl 用固定 id，render() 重建 DOM 后能重新找到
    const thinkingId = "kb-thinking-" + commentId;

    function ensureThinking(text) {
      let el = document.getElementById(thinkingId);
      if (!el) {
        const card = document.getElementById("kb-cmt-" + commentId);
        if (!card) return;
        el = document.createElement("div");
        el.id = thinkingId;
        el.className = "kb-thinking";
        card.appendChild(el);
      }
      el.textContent = text;
    }
    function removeThinking() {
      const el = document.getElementById(thinkingId);
      if (el) el.remove();
    }

    ensureThinking("AI 思考中...");

    try {
      let agentCommentId = c.agentCommentId;

      // 构建完整对话历史作为 comment（首轮 + 所有追问）
      const allUserMessages = [c.text, ...c.replies.filter(r => !r.isAI).map(r => r.text)];
      const conversationComment = allUserMessages.join("\n\n---追问---\n\n");

      // 记录 rerun 前已有的 agent reply 数量，轮询时等待新增
      let existingAgentReplyCount = 0;

      // 如果已有 agentCommentId，rerun with latest conversation；否则新建
      if (agentCommentId) {
        // 先查当前有多少条 agent reply
        try {
          const preResp = await fetch(`http://localhost:8766/comments/${agentCommentId}`);
          if (preResp.ok) {
            const preData = await preResp.json();
            existingAgentReplyCount = preData.replies.filter(r => r.author === "agent").length;
          }
        } catch { /* ignore */ }
        // 更新 comment 内容为完整对话再 rerun
        const patchResp = await fetch(`http://localhost:8766/comments/${agentCommentId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ comment: conversationComment }),
        });
        if (!patchResp.ok) throw new Error(`无法更新追问内容（HTTP ${patchResp.status}）`);
        const rerunResp = await fetch(`http://localhost:8766/comments/${agentCommentId}/rerun`, { method: "POST" });
        if (!rerunResp.ok) throw new Error(`无法重新召唤 AI（HTTP ${rerunResp.status}）`);
      } else {
        const surrounding = getSurroundingText(c.excerpt || "");
        const resp = await fetch("http://localhost:8766/comments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page_url: location.href,
            page_title: document.title,
            selected_text: c.excerpt || "",
            surrounding_text: surrounding,
            comment: conversationComment,
            no_agent: false,
          }),
        });
        if (!resp.ok) throw new Error("agent_api 不可用");
        const data = await resp.json();
        agentCommentId = data.id;
        const fresh = load();
        const match = fresh.find(x => x.id === commentId);
        if (match) { match.agentCommentId = agentCommentId; save(fresh); }
      }

      // 轮询等待 agent 回复（每5秒，最多360次=30分钟）
      let reply = null;
      let replyDebugMeta = null;
      const startPoll = Date.now();
      // 根据 agent 类型和经过时间显示有意义的状态
      const agentType = (() => {
        const freshComments = load();
        const fc = freshComments.find(x => x.id === commentId);
        return fc ? (fc.text.match(/@(调研|竞品|思辨|解释)/)?.[1] || "思辨") : "思辨";
      })();
      const phases = agentType === "调研" || agentType === "竞品"
        ? [
            [0,  "🤖 Agent 已派出，正在加载上下文..."],
            [8,  "🔍 正在搜索相关信息（GitHub / ProductHunt / 36kr）..."],
            [30, "📊 正在整理搜索结果..."],
            [60, "🧠 正在综合分析，深度调研需要一些时间..."],
            [120,"⏳ 仍在处理，复杂调研可能需要 5-10 分钟..."],
          ]
        : [
            [0,  "🤖 Agent 已派出，正在思考..."],
            [15, "🧠 正在深入分析..."],
            [60, "⏳ 思考时间有点长，内容比较复杂..."],
          ];
      for (let i = 0; i < 360; i++) {
        await new Promise(r => setTimeout(r, 5000));
        const elapsed = Math.round((Date.now() - startPoll) / 1000);
        // 找当前阶段文字
        let phaseText = phases[0][1];
        for (const [threshold, text] of phases) {
          if (elapsed >= threshold) phaseText = text;
        }
        ensureThinking(`${phaseText} (${elapsed}s)`);
        const pollResp = await fetch(`http://localhost:8766/comments/${agentCommentId}`);
        if (!pollResp.ok) continue;
        const data = await pollResp.json();
        const agentReplies = data.replies.filter(r => r.author === "agent");
        // 等待新增的 reply（多轮追问时 rerun 前已有旧 reply）
        if (agentReplies.length > existingAgentReplyCount) {
          const lastReply = agentReplies[agentReplies.length - 1];
          reply = lastReply.content;
          replyDebugMeta = lastReply.debug_meta || null;
          break;
        }
      }

      if (reply) {
        const shouldNotify = !_isCommentCardVisible(commentId);
        addReply(commentId, reply, true, replyDebugMeta);
        if (shouldNotify) _markAiReplyReady(commentId);
        // 每次 AI 回复后更新 Notion（upsert：有 page 则追加，无则新建）
        const freshC = load().find(x => x.id === commentId);
        if (freshC) updateNotionPage(freshC);
      } else {
        addReply(commentId, "AI 仍在处理中，请稍候刷新页面查看结果。", true);
      }
    } catch (err) {
      const msg = err.message || String(err);
      if (msg.includes("Extension context invalidated")) {
        addReply(commentId, "AI 回复失败：插件已失效，请刷新页面后重试。", true);
      } else if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
        addReply(commentId, "AI 回复失败：无法连接本地服务。请检查是否已运行 start.sh 启动后端（终端执行：bash start.sh）", true);
      } else {
        addReply(commentId, "AI 回复失败：" + msg, true);
      }
    }

    removeThinking();
    _askAIRunning.delete(commentId);
    updateCommentCard(commentId);
    _lockReplyInput(commentId, false);
  }

  // ── 对外接口：打开评论面板（高亮由调用方处理）──
  // 划线后调用此函数：展开输入区，让用户写一条评论
  function open(excerpt, url, title) {
    currentExcerpt = excerpt;
    buildPanel();
    // 更新输入区 quote 预览
    const qp = document.getElementById("kb-cp-quote-preview");
    if (qp) {
      if (excerpt && excerpt.trim()) {
        qp.textContent = `"${excerpt.slice(0, 80)}${excerpt.length > 80 ? "…" : ""}"`;
        qp.style.display = "block";
      } else {
        qp.textContent = "";
        qp.style.display = "none";
      }
    }
    // 清空输入框
    const ta = document.getElementById("kb-cp-textarea");
    if (ta) ta.value = "";
    panelOpen = true;
    panelEl.classList.remove("kb-btn-hidden");
    document.body.style.marginRight = "360px";
    render();
    syncVisibleCommentsFromBackend({ notify: false });
    updateBadge();
    // 展开输入区 + 聚焦（这是划线触发的）
    expandInputArea(true);
  }

  // 页面加载时恢复高亮 + 渲染已有评论
  function init() {
    // 关键：stylesheet 必须先注入，否则 restoreHighlights 重建出来的 mark 会失去 .kb-comment-highlight 的样式（淡橙底）
    injectStyles();
    restoreHighlights();
    restoreCommentHighlights();
    const comments = load();
    if (comments.length > 0) {
      buildPanel();
      panelEl.classList.add("kb-btn-hidden");
      render();
      syncVisibleCommentsFromBackend({ notify: true });
    }
    // badge 响应式更新：有高亮或评论时常驻显示
    updateBadge();
    setTimeout(capturePageExposureIfAllowed, 2500);

    // 检测受限 iframe（如 ChatGPT Deep Research），DOM 动态插入时也能捕获
    if (!_iframeHinted) {
      const _checkIframe = () => {
        if (_iframeHinted) return;
        const found = [...document.querySelectorAll("iframe")].some(
          f => (f.title || "").includes("deep-research")
        );
        if (found) {
          _iframeHinted = true;
          showToast("此页面内容在受限区域，划线请用右键菜单", "info");
        }
      };
      _checkIframe(); // 立即检查一次
      const obs = new MutationObserver(_checkIframe);
      obs.observe(document.body, { childList: true, subtree: true });
      // 30秒后停止观察，避免长期性能开销
      setTimeout(() => obs.disconnect(), 30000);
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
    if (e.data && e.data.__kb_test === 'simulate_unread_ai_reply') {
      debugMarkUnreadAiReply();
    }
  });

  return {
    open,
    render,
    load,
    doHighlight,
    doHighlightAndOpenComment,
    highlightByText,
    saveHighlightToNotion,
    debugMarkUnreadAiReply,
    version: KB_CONTENT_VERSION,
  };
})();
