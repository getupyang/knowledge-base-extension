const KB_CONTENT_VERSION = "0.3.18-margin-share-playground";
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
    commentSystem.saveHighlightToVault(msg.excerpt, msg.title, msg.url, msg.platform);
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
    background:${type === "success" ? "oklch(0.55 0.14 150)" : "#ef4444"};
    box-shadow:0 4px 12px rgba(0,0,0,0.15); transition:opacity 0.3s;
  `;
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 3000);
}

function isWindowsPlatform() {
  const platform = (
    (navigator.userAgentData && navigator.userAgentData.platform) ||
    navigator.platform ||
    navigator.userAgent ||
    ""
  );
  return /win/i.test(platform);
}

function truncate(str, max) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "..." : str;
}

function kbRandomId(prefix) {
  const raw = (globalThis.crypto && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}_${raw}`;
}

const KB_TELEMETRY_INSTALL_KEY = "kb_telemetry_install_id_v1";
const KB_TELEMETRY_SESSION_KEY = "kb_telemetry_session_v1";
const KB_TELEMETRY_SESSION_TTL_MS = 30 * 60 * 1000;
// install_id 放 chrome.storage.local（跨域名共享），保证一台机器 = 一个 UUID
// session_id 仍走 localStorage（per origin），跨网站会算多个 session，但 30min idle TTL 让影响有限

// 噪声参数：utm_*/from/source/ref/referrer/spm/gclid/fbclid 等追踪/导流参数
// page_id 设计：同一 URL（剥噪声后）→ 同一 page_id，方便把同页连续动作串起来；不存 URL 原文
const KB_TELEMETRY_NOISE_PARAMS = new Set([
  "from", "source", "ref", "referrer", "spm", "gclid", "fbclid",
]);
function kbNormalizeUrlForHash(rawUrl) {
  try {
    const u = new URL(rawUrl);
    u.hash = "";  // 剥 fragment
    // 剥 utm_*
    const toDelete = [];
    u.searchParams.forEach((_, k) => {
      const lk = k.toLowerCase();
      if (lk.startsWith("utm_") || KB_TELEMETRY_NOISE_PARAMS.has(lk)) toDelete.push(k);
    });
    toDelete.forEach((k) => u.searchParams.delete(k));
    u.hostname = u.hostname.toLowerCase();
    // 末尾斜杠
    let s = u.toString();
    if (s.endsWith("/") && u.pathname !== "/") s = s.slice(0, -1);
    return s.trim();
  } catch {
    return String(rawUrl || "").trim();
  }
}
let _kbPageIdCache = null;
async function getTelemetryPageId() {
  if (_kbPageIdCache) return _kbPageIdCache;
  try {
    const normalized = kbNormalizeUrlForHash(location.href);
    const buf = new TextEncoder().encode(normalized);
    const digest = await crypto.subtle.digest("SHA-256", buf);
    const hex = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    _kbPageIdCache = `page_${hex.slice(0, 28)}`;  // 28 hex + "page_" 前缀 = 33 字符
  } catch {
    _kbPageIdCache = kbRandomId("page");  // 极端兜底
  }
  return _kbPageIdCache;
}

// Environment 字段：每个事件都带
function getTelemetryEnv() {
  let browser = "";
  try {
    const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
    const main = brands.find((b) => /Chrome|Edge|Brave|Opera|Arc|Firefox/i.test(b.brand));
    browser = (main && main.brand) || "";
  } catch {}
  if (!browser) {
    const ua = navigator.userAgent || "";
    if (/Edg\//i.test(ua)) browser = "Edge";
    else if (/Chrome\//i.test(ua)) browser = "Chrome";
    else if (/Firefox\//i.test(ua)) browser = "Firefox";
    else if (/Safari\//i.test(ua)) browser = "Safari";
  }
  let os = "";
  try {
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/mac/i.test(p)) os = "macOS";
    else if (/win/i.test(p)) os = "Windows";
    else if (/linux/i.test(p)) os = "Linux";
    else if (/cros/i.test(p)) os = "ChromeOS";
  } catch {}
  let extensionId = "";
  try { extensionId = (chrome.runtime && chrome.runtime.id) || ""; } catch {}
  return {
    app_version: KB_CONTENT_VERSION,
    extension_id: extensionId,
    browser: browser || "Unknown",
    os: os || "Unknown",
    locale: navigator.language || "",
  };
}

// install_id 缓存：模块加载时立刻发起读取，命中后填充。所有 telemetry 调用都用 async getter。
let _kbInstallIdCache = null;
let _kbInstallIdPromise = null;
function _loadInstallIdFromStorage() {
  if (_kbInstallIdPromise) return _kbInstallIdPromise;
  _kbInstallIdPromise = new Promise((resolve) => {
    try {
      chrome.storage.local.get([KB_TELEMETRY_INSTALL_KEY], (r) => {
        let id = (r && r[KB_TELEMETRY_INSTALL_KEY]) || "";
        if (!id) {
          // 迁移：旧版把 install_id 写在 localStorage，这里搬过去而不是另起一个 UUID
          let legacy = "";
          try { legacy = localStorage.getItem(KB_TELEMETRY_INSTALL_KEY) || ""; } catch {}
          id = legacy || kbRandomId("install");
          try {
            chrome.storage.local.set({ [KB_TELEMETRY_INSTALL_KEY]: id });
          } catch {}
        }
        _kbInstallIdCache = id;
        resolve(id);
      });
    } catch {
      // 极端兜底：chrome.storage 不可用时回退 localStorage
      let id = "";
      try { id = localStorage.getItem(KB_TELEMETRY_INSTALL_KEY) || ""; } catch {}
      if (!id) {
        id = kbRandomId("install");
        try { localStorage.setItem(KB_TELEMETRY_INSTALL_KEY, id); } catch {}
      }
      _kbInstallIdCache = id;
      resolve(id);
    }
  });
  return _kbInstallIdPromise;
}
_loadInstallIdFromStorage();  // 触发预加载
async function getTelemetryInstallId() {
  if (_kbInstallIdCache) return _kbInstallIdCache;
  return await _loadInstallIdFromStorage();
}

function getTelemetrySessionId() {
  const now = Date.now();
  let state = null;
  try { state = JSON.parse(localStorage.getItem(KB_TELEMETRY_SESSION_KEY) || "null"); } catch {}
  if (!state || !state.id || now - Number(state.lastSeen || 0) > KB_TELEMETRY_SESSION_TTL_MS) {
    state = { id: kbRandomId("session"), lastSeen: now };
  } else {
    state.lastSeen = now;
  }
  try { localStorage.setItem(KB_TELEMETRY_SESSION_KEY, JSON.stringify(state)); } catch {}
  return state.id;
}

function charBucket(value) {
  const n = typeof value === "number" ? value : String(value || "").length;
  if (n <= 0) return "0";
  if (n <= 20) return "1-20";
  if (n <= 80) return "21-80";
  if (n <= 200) return "81-200";
  if (n <= 500) return "201-500";
  if (n <= 1000) return "501-1000";
  return "1000+";
}

function providerMode(provider) {
  if (provider === "api") return "user_api";
  if (provider === "claude_code" || provider === "codex_cli") return "local_cli";
  if (provider === "mock") return "mock";
  return "unknown";
}

function parseMaybeJson(value) {
  if (!value) return {};
  if (typeof value !== "string") return value || {};
  try { return JSON.parse(value) || {}; } catch { return {}; }
}

function summarizeAiDebugMeta(debugMeta) {
  const dm = parseMaybeJson(debugMeta);
  const call = dm.llm?.answer_call || dm.llm?.step2_call || {};
  const provider = call.provider || dm.llm_provider || "";
  return {
    stage: call.stage || "answer",
    status: call.status || dm.status || "",
    provider_mode: call.provider_mode || providerMode(provider),
    provider,
    provider_config: call.provider_config || "",
    local_agent: call.local_agent || "",
    api_provider: call.api_provider || dm.llm_api_provider || "",
    model: call.model || dm.llm_model || "",
    fallback_used: Boolean(call.fallback_used),
    fallback_from: call.fallback_from || "",
    answer_source: dm.llm?.answer_source || "",
    agent_role: dm.role || "",
    intent: dm.intent || "",
    is_quick: Boolean(dm.is_quick),
    is_plan: Boolean(dm.is_plan),
    elapsed_s: call.elapsed_s ?? dm.elapsed_s,
    prompt_tokens_est: call.prompt_tokens_est ?? dm.prompt_tokens_est,
    input_tokens: call.input_tokens,
    output_tokens: call.output_tokens,
    total_tokens: call.total_tokens,
    cache_tokens: call.cache_tokens,
    cost_usd: call.cost_usd,
    usage_source: call.usage_source,
    error_category: call.error_category,
    error_code: call.error_code,
  };
}

function categorizeClientError(msg) {
  const s = String(msg || "").toLowerCase();
  if (s.includes("failed to fetch") || s.includes("networkerror")) return { error_category: "network", error_code: "backend_unreachable" };
  if (s.includes("timeout")) return { error_category: "timeout", error_code: "client_timeout" };
  if (s.includes("http 401")) return { error_category: "auth", error_code: "unauthorized" };
  if (s.includes("http 429")) return { error_category: "rate_limit", error_code: "rate_limit" };
  if (s.includes("extension context invalidated")) return { error_category: "extension", error_code: "context_invalidated" };
  return { error_category: "unknown", error_code: "unknown" };
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
  let selectionTimer = null;
  let lastPointer = null;
  let savedExcerpt = "";
  let savedRange = null;
  let savedTextResolver = null;

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
        background: oklch(0.55 0.14 150) !important;
        color: oklch(0.985 0.006 125) !important;
        border-radius: 4px;
        padding: 4px 5px;
        box-shadow: 0 6px 20px rgba(40,30,20,0.22);
        font-family: "Inter Tight", "Inter", -apple-system, sans-serif;
        pointer-events: all;
        white-space: nowrap;
        transition: opacity 0.15s;
        box-sizing: border-box;
        isolation: isolate;
      }
      #kb-sel-bar button {
        -webkit-appearance: none !important;
        appearance: none !important;
        background: transparent !important;
        border: 0 !important;
        color: oklch(0.985 0.006 125) !important;
        font-size: 12px !important;
        line-height: 1.2 !important;
        padding: 4px 10px !important;
        border-radius: 3px !important;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
        font-family: "Inter Tight", "Inter", -apple-system, sans-serif !important;
        transition: background 0.12s;
        letter-spacing: 0.04em !important;
        text-transform: none !important;
        box-sizing: border-box !important;
      }
      #kb-sel-bar button:hover { background: oklch(0.48 0.13 150) !important; }
      #kb-sel-bar .kb-bar-divider {
        width: 1px; background: oklch(0.72 0.08 150);
        margin: 3px 1px; border-radius: 1px;
      }
    `;
    document.head.appendChild(s);
  }

  function _rememberPointer(e) {
    if (!e || typeof e.clientX !== "number" || typeof e.clientY !== "number") return;
    lastPointer = { clientX: e.clientX, clientY: e.clientY, ts: Date.now() };
  }

  function _isUsableRect(rect) {
    if (!rect) return false;
    const left = Number(rect.left);
    const top = Number(rect.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return false;
    return Math.max(Number(rect.width) || 0, Number(rect.right) - Number(rect.left) || 0) > 0 ||
      Math.max(Number(rect.height) || 0, Number(rect.bottom) - Number(rect.top) || 0) > 0;
  }

  function _rectFromPoint(point) {
    if (!point) return null;
    return {
      left: point.clientX - 1,
      right: point.clientX + 1,
      top: point.clientY - 1,
      bottom: point.clientY + 1,
      width: 2,
      height: 2,
    };
  }

  function _rectFromRange(range) {
    if (!range) return null;
    try {
      const rects = Array.from(range.getClientRects ? range.getClientRects() : [])
        .filter(_isUsableRect)
        .sort((a, b) => (b.width * b.height) - (a.width * a.height));
      if (rects[0]) return rects[0];
      const rect = range.getBoundingClientRect ? range.getBoundingClientRect() : null;
      if (_isUsableRect(rect)) return rect;
    } catch {}
    return null;
  }

  function _isPdfLikePage(target) {
    const url = location.href.toLowerCase();
    const path = location.pathname.toLowerCase();
    if (document.contentType === "application/pdf") return true;
    if (path.endsWith(".pdf") || url.includes(".pdf?") || url.includes(".pdf#")) return true;
    if (target && target.closest?.("pdf-viewer, embed[type='application/pdf'], #viewerContainer, .textLayer, .page[data-page-number]")) return true;
    return Boolean(document.querySelector("pdf-viewer, embed[type='application/pdf'], #viewerContainer, .textLayer, .page[data-page-number]"));
  }

  function _isWeReadReaderPage() {
    return location.hostname === "weread.qq.com" && location.pathname.startsWith("/web/reader/");
  }

  function _rectFromElement(el) {
    if (!el) return null;
    try {
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return null;
      const rect = el.getBoundingClientRect();
      if (!_isUsableRect(rect)) return null;
      return rect;
    } catch {
      return null;
    }
  }

  function _unionRects(rects) {
    const usable = rects.filter(_isUsableRect);
    if (!usable.length) return null;
    const left = Math.min(...usable.map(r => r.left));
    const top = Math.min(...usable.map(r => r.top));
    const right = Math.max(...usable.map(r => r.right));
    const bottom = Math.max(...usable.map(r => r.bottom));
    return {
      left,
      top,
      right,
      bottom,
      width: right - left,
      height: bottom - top,
    };
  }

  function _readWeReadSelection() {
    if (!_isWeReadReaderPage()) return null;
    const rect = _unionRects(Array.from(document.querySelectorAll(".wr_selection")).map(_rectFromElement));
    if (!_isUsableRect(rect)) return null;
    return {
      text: "",
      range: null,
      rect,
      textResolver: _resolveWeReadSelectionText,
    };
  }

  async function _resolveWeReadSelectionText() {
    const copyButton = document.querySelector(".reader_toolbar_container .toolbarItem.wr_copy");
    if (!copyButton) throw new Error("未找到微信读书的复制按钮");

    const actionId = kbRandomId("weread_copy");
    const copiedText = await _captureWeReadCopyText(actionId);

    const text = String(copiedText || "").trim();
    if (text.length < 3) throw new Error("没有读到微信读书选中文本");
    return text;
  }

  function _captureWeReadCopyText(actionId) {
    return _ensureWeReadCopyBridge().then((ready) => {
      if (!ready) return "";
      return new Promise((resolve) => {
        let done = false;
        const finish = (text) => {
          if (done) return;
          done = true;
          window.removeEventListener("message", onMessage);
          resolve(String(text || ""));
        };
        const onMessage = (event) => {
          if (event.source !== window) return;
          const data = event.data || {};
          if (data.__kb_weread_copy_capture !== actionId) return;
          finish(data.text);
        };
        window.addEventListener("message", onMessage);
        window.postMessage({ __kb_weread_copy_capture_request: actionId }, "*");
        setTimeout(() => finish(""), 1200);
      });
    });
  }

  let wereadBridgePromise = null;
  function _ensureWeReadCopyBridge() {
    if (!_isWeReadReaderPage()) return Promise.resolve(false);
    if (document.getElementById("kb-weread-copy-bridge")) return Promise.resolve(true);
    if (wereadBridgePromise) return wereadBridgePromise;
    wereadBridgePromise = new Promise((resolve) => {
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        resolve(ok);
      };
      const script = document.createElement("script");
      script.id = "kb-weread-copy-bridge";
      script.src = chrome.runtime.getURL("src/content/weread-bridge.js");
      script.onload = () => finish(true);
      script.onerror = () => finish(false);
      (document.documentElement || document.head || document.body).appendChild(script);
      setTimeout(() => finish(Boolean(document.getElementById("kb-weread-copy-bridge"))), 1200);
    });
    return wereadBridgePromise;
  }

  function _targetClosest(target, selector) {
    return target && target.nodeType === Node.ELEMENT_NODE && target.closest?.(selector);
  }

  function _readSelection(e) {
    const sel = window.getSelection?.();
    const text = sel?.toString().trim() || "";
    if (text.length < 3) return null;

    let range = null;
    try {
      if (sel.rangeCount > 0) range = sel.getRangeAt(0);
    } catch {
      range = null;
    }

    let rect = _rectFromRange(range);
    if (!_isUsableRect(rect)) rect = _rectFromPoint(e);
    if (!_isUsableRect(rect) && lastPointer && Date.now() - lastPointer.ts < 5000) {
      rect = _rectFromPoint(lastPointer);
    }
    if (!_isUsableRect(rect)) return null;
    return { text, range, rect };
  }

  function _showFromSelection(e, { hideOnEmpty = true } = {}) {
    const info = _readSelection(e) || _readWeReadSelection();
    if (!info) {
      if (hideOnEmpty) hide();
      return false;
    }
    show(info.rect, info.text, info.range, { textResolver: info.textResolver });
    return true;
  }

  function _scheduleShowFromSelection(e, options = {}) {
    _rememberPointer(e);
    clearTimeout(selectionTimer);
    selectionTimer = setTimeout(() => _showFromSelection(e, options), options.delay ?? 160);
  }

  async function _getSavedExcerpt() {
    if (typeof savedTextResolver === "function") {
      const resolved = await savedTextResolver();
      savedExcerpt = resolved;
    }
    return savedExcerpt;
  }

  function show(rect, excerpt, range, options = {}) {
    injectStyles();
    hide();
    savedExcerpt = excerpt;
    // 克隆 range 以免 selection 清除后失效
    savedRange = range ? range.cloneRange() : null;
    savedTextResolver = options.textResolver || null;

    barEl = document.createElement("div");
    barEl.id = "kb-sel-bar";
    barEl.innerHTML = `
      <button id="kb-bar-highlight" title="仅保存高亮">高亮</button>
      <div class="kb-bar-divider"></div>
      <button id="kb-bar-comment" title="高亮并评注">评注</button>
    `;

    document.body.appendChild(barEl);
    ["pointerdown", "mousedown"].forEach(type => {
      barEl.addEventListener(type, (e) => {
        e.stopPropagation();
        e.preventDefault();
      }, true);
    });

    // 定位到选区下方
    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;
    const barW = 160; // 估算宽度，实际渲染后会自适应
    let left = rect.left + scrollX + rect.width / 2 - barW / 2;
    let top = rect.bottom + scrollY + 8;
    if (left < 8) left = 8;
    barEl.style.left = left + "px";
    barEl.style.top = top + "px";

    barEl.querySelector("#kb-bar-highlight").addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const excerpt = await _getSavedExcerpt();
        hide();
        if (savedRange) _lastSelectionSurrounding = captureSurroundingFromRange(savedRange);
        commentSystem.doHighlight(excerpt, savedRange);
        commentSystem.saveHighlightToVault(excerpt, document.title, location.href, null);
      } catch (err) {
        console.warn("[KB] selection bar could not read selected text:", err);
        showToast(`没有读到选中文本：${err.message || "请重试"}`, "error");
      }
    });
    barEl.querySelector("#kb-bar-comment").addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const excerpt = await _getSavedExcerpt();
        hide();
        // 划线时用 Range 精确捕获前后文（在 Range 还有效的时候）
        if (savedRange) _lastSelectionSurrounding = captureSurroundingFromRange(savedRange);
        commentSystem.doHighlightAndOpenComment(excerpt, savedRange);
      } catch (err) {
        console.warn("[KB] selection bar could not read selected text:", err);
        showToast(`没有读到选中文本：${err.message || "请重试"}`, "error");
      }
    });

    // 3秒无操作自动消失
    hideTimer = setTimeout(hide, 3000);
    barEl.addEventListener("mouseenter", () => { clearTimeout(hideTimer); });
    barEl.addEventListener("mouseleave", () => { hideTimer = setTimeout(hide, 1500); });
  }

  function hide() {
    clearTimeout(hideTimer);
    clearTimeout(selectionTimer);
    if (barEl) { barEl.remove(); barEl = null; }
    savedTextResolver = null;
  }

  function _startWeReadObserver() {
    if (!_isWeReadReaderPage() || !document.body) return;
    const schedule = () => _scheduleShowFromSelection(null, { hideOnEmpty: false, delay: 120 });
    const obs = new MutationObserver((mutations) => {
      if (mutations.some(m => {
        const target = m.target;
        if (target?.nodeType === Node.ELEMENT_NODE && target.matches?.(".wr_selection")) return true;
        return Array.from(m.addedNodes || []).some(n =>
          n.nodeType === Node.ELEMENT_NODE && (n.matches?.(".wr_selection") || n.querySelector?.(".wr_selection"))
        );
      })) {
        schedule();
      }
    });
    obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
  }

  document.addEventListener("pointerdown", _rememberPointer, true);
  document.addEventListener("pointerup", (e) => {
    if (barEl && barEl.contains(e.target)) return;
    if (_targetClosest(e.target, "#kb-comment-panel")) return;
    if (_isPdfLikePage(e.target) || _isWeReadReaderPage()) _scheduleShowFromSelection(e, { hideOnEmpty: false, delay: 80 });
  }, true);

  // mouseup 监听：有选中文字时显示 bar
  // 用 capture:true 在捕获阶段触发，避免 SPA（ChatGPT 等）在冒泡阶段 stopPropagation 导致事件丢失
  document.addEventListener("mouseup", (e) => {
    // 如果点击在 bar 自身上，不处理
    if (barEl && barEl.contains(e.target)) return;
    // 如果点击在评论面板上，不处理
    if (_targetClosest(e.target, "#kb-comment-panel")) return;
    _scheduleShowFromSelection(e, { hideOnEmpty: true, delay: 200 }); // 延迟，避免与页面自带菜单冲突

  }, true);

  document.addEventListener("selectionchange", () => {
    if (!_isPdfLikePage(document.activeElement)) return;
    _scheduleShowFromSelection(null, { hideOnEmpty: false, delay: 120 });
  }, true);

  document.addEventListener("keyup", (e) => {
    if (!_isPdfLikePage(e.target)) return;
    _scheduleShowFromSelection(e, { hideOnEmpty: false, delay: 80 });
  }, true);

  // 点击页面其他地方收起 bar
  document.addEventListener("mousedown", (e) => {
    if (barEl && !barEl.contains(e.target)) hide();
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _startWeReadObserver, { once: true });
  } else {
    _startWeReadObserver();
  }

  return { hide };
})();

// ─── 评论系统 ────────────────────────────────────────────────

const DEBUG_MODE = true; // 发布时改为 false

const commentSystem = (() => {
  function _pageUrlWithoutHash(url = location.href) {
    return String(url || "").split("#")[0];
  }
  function _pageUrlWithoutQuery(url = location.href) {
    return _pageUrlWithoutHash(url).split("?")[0];
  }
  function _withoutTrailingSlash(url) {
    return String(url || "").replace(/\/$/, "");
  }
  function _withTrailingSlash(url) {
    const s = String(url || "");
    return s.endsWith("/") ? s : `${s}/`;
  }
  function _unique(values) {
    return Array.from(new Set(values.filter(Boolean)));
  }
  function _pageUrlCandidates() {
    const full = _pageUrlWithoutHash(location.href);
    const base = _pageUrlWithoutQuery(location.href);
    return _unique([
      full,
      base,
      _withoutTrailingSlash(full),
      _withTrailingSlash(full),
      _withoutTrailingSlash(base),
      _withTrailingSlash(base),
    ]);
  }
  function _storageUrl() {
    return _withoutTrailingSlash(_pageUrlWithoutQuery(location.href));
  }
  function _storageKeys(prefix) {
    return _unique(_pageUrlCandidates().map(url => prefix + url).concat(prefix + _storageUrl()));
  }
  const STORAGE_KEY = () => "kb_comments_" + _storageUrl();
  let panelEl = null;
  let currentExcerpt = "";
  let panelOpen = false;
  let shareDialogEl = null;
  let shareDialogState = null;
  const _aiUnreadCommentIds = new Set();
  const PANEL_WIDTH_KEY = "kb_comment_panel_width_v1";
  const PANEL_DEFAULT_WIDTH = 380;
  const PANEL_MIN_WIDTH = 320;
  const PANEL_MAX_WIDTH = 820;
  const SHARE_CLOSER = "Keep your thinking in Margin.";
  const SHARE_INSTALL_TEXT = "get-margin.vercel.app";
  const SHARE_BRAND = "#1E7A3C";
  const SHARE_INK = "#18221B";
  const SHARE_INK_2 = "#26332A";
  const SHARE_MUTED = "#53665A";
  const SHARE_MUTED_2 = "#6C7C70";
  const SHARE_HAIR = "rgba(39,86,58,0.18)";
  const SHARE_PAPER = "#F7F8F3";
  let panelWidth = _readPanelWidth();
  let shareMarkPromise = null;
  let shareQrPromise = null;
  let panelResizeActive = false;

  function _panelViewportMaxWidth() {
    const viewport = Math.max(window.innerWidth || 0, document.documentElement?.clientWidth || 0, PANEL_DEFAULT_WIDTH);
    return Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, viewport - 72));
  }

  function _clampPanelWidth(value) {
    const n = Number.parseInt(value, 10);
    const width = Number.isFinite(n) ? n : PANEL_DEFAULT_WIDTH;
    return Math.min(Math.max(width, PANEL_MIN_WIDTH), _panelViewportMaxWidth());
  }

  function _readPanelWidth() {
    try {
      return _clampPanelWidth(localStorage.getItem(PANEL_WIDTH_KEY) || PANEL_DEFAULT_WIDTH);
    } catch {
      return _clampPanelWidth(PANEL_DEFAULT_WIDTH);
    }
  }

  function _applyPanelWidth(value = panelWidth, options = {}) {
    panelWidth = _clampPanelWidth(value);
    if (options.persist) {
      try { localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidth)); } catch {}
    }
    if (panelEl) {
      panelEl.style.setProperty("--kb-panel-width", `${panelWidth}px`);
    }
    if (panelOpen && panelEl && !panelEl.classList.contains("kb-btn-hidden")) {
      document.body.style.marginRight = `${panelWidth}px`;
    }
  }

  function _showPanel() {
    if (!panelEl) buildPanel();
    if (!panelEl) return;
    panelOpen = true;
    panelEl.classList.remove("kb-btn-hidden");
    _applyPanelWidth(panelWidth);
    updateBadge();
  }

  function _hidePanel() {
    panelOpen = false;
    if (panelEl) panelEl.classList.add("kb-btn-hidden");
    document.body.style.marginRight = "";
    updateBadge();
  }

  function _syncPanelWidthToViewport() {
    if (!panelEl) return;
    _applyPanelWidth(panelWidth, { persist: true });
  }

  function _startPanelResize(event) {
    if (event.type === "mousedown" && typeof event.button === "number" && event.button > 0) return;
    if (panelResizeActive) return;
    panelResizeActive = true;
    event.preventDefault();
    document.documentElement.classList.add("kb-panel-resizing");
    const move = (e) => {
      const point = e.touches?.[0] || e.changedTouches?.[0] || e;
      if (e.cancelable) e.preventDefault();
      const viewport = window.innerWidth || document.documentElement.clientWidth || PANEL_DEFAULT_WIDTH;
      _applyPanelWidth(viewport - point.clientX);
    };
    const finish = () => {
      panelResizeActive = false;
      document.documentElement.classList.remove("kb-panel-resizing");
      _applyPanelWidth(panelWidth, { persist: true });
      window.removeEventListener("mousemove", move, true);
      window.removeEventListener("mouseup", finish, true);
      window.removeEventListener("touchmove", move, true);
      window.removeEventListener("touchend", finish, true);
      window.removeEventListener("touchcancel", finish, true);
    };
    window.addEventListener("mousemove", move, true);
    window.addEventListener("mouseup", finish, true);
    window.addEventListener("touchmove", move, { capture: true, passive: false });
    window.addEventListener("touchend", finish, true);
    window.addEventListener("touchcancel", finish, true);
  }

  function ensureThreadTelemetryId(comment) {
    if (!comment) return "";
    if (comment.telemetryThreadId) return comment.telemetryThreadId;
    const id = kbRandomId("thread");
    comment.telemetryThreadId = id;
    const comments = load();
    const match = comments.find(x => String(x.id) === String(comment.id));
    if (match) {
      match.telemetryThreadId = id;
      save(comments);
    }
    return id;
  }

  function threadTurnCount(comment) {
    return (comment?.replies || []).length;
  }

  async function _postTelemetry(body, attempt = 0) {
    try {
      const resp = await fetch("http://localhost:8766/telemetry/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok && attempt === 0) {
        // 重试 1 次，500ms 后
        await new Promise((r) => setTimeout(r, 500));
        return _postTelemetry(body, 1);
      }
      return resp.ok;
    } catch {
      if (attempt === 0) {
        await new Promise((r) => setTimeout(r, 500));
        return _postTelemetry(body, 1);
      }
      return false;
    }
  }

  async function telemetryEvent(eventName, surface, properties = {}, comment = null) {
    try {
      const [pageId, installId] = await Promise.all([
        getTelemetryPageId(), getTelemetryInstallId(),
      ]);
      const envProps = getTelemetryEnv();
      const body = {
        event_name: eventName,
        anonymous_install_id: installId,
        app_session_id: getTelemetrySessionId(),
        page_id: pageId,
        thread_telemetry_id: comment ? ensureThreadTelemetryId(comment) : "",
        surface,
        properties: { ...envProps, ...properties },
      };
      return await _postTelemetry(body);
    } catch {
      return false;
    }
  }

  async function telemetryIdentity(comment = null, surface = "sidebar") {
    const [pageId, installId] = await Promise.all([
      getTelemetryPageId(), getTelemetryInstallId(),
    ]);
    return {
      anonymous_install_id: installId,
      app_session_id: getTelemetrySessionId(),
      page_id: pageId,
      thread_telemetry_id: comment ? ensureThreadTelemetryId(comment) : "",
      source_surface: surface,
    };
  }

  // page_opened 已废弃：iframe/PDF/SPA 噪声大，且产品价值低。
  // "用户活跃度"看 highlight_saved / sidebar_comment_created / ai_reply_requested 的 install_id distinct 即可。

  // ── 提前保存 selection（提交时 selection 已消失，需要提前捕获）──
  let _savedSelection = "";
  document.addEventListener("mouseup", () => {
    const sel = window.getSelection()?.toString().trim() || "";
    if (sel.length > 5) _savedSelection = sel;
  });

  // ── 持久化 ──
  function _commentMergeKey(c) {
    if (c?.agentCommentId) return `backend:${c.agentCommentId}`;
    return `local:${c?.id || ""}:${c?.excerpt || ""}:${c?.text || ""}`;
  }
  function load() {
    const seen = new Set();
    const merged = [];
    for (const key of _storageKeys("kb_comments_")) {
      let items = [];
      try { items = JSON.parse(localStorage.getItem(key) || "[]"); } catch { items = []; }
      if (!Array.isArray(items)) continue;
      for (const item of items) {
        const mergeKey = _commentMergeKey(item);
        if (seen.has(mergeKey)) continue;
        seen.add(mergeKey);
        merged.push(item);
      }
    }
    return merged;
  }
  function save(comments) {
    localStorage.setItem(STORAGE_KEY(), JSON.stringify(comments));
  }
  function addComment(excerpt, text) {
    const comments = load();
    const c = {
      id: Date.now(),
      telemetryThreadId: kbRandomId("thread"),
      excerpt,
      text,
      createdAt: new Date().toISOString(),
      replies: [],
    };
    comments.unshift(c);
    save(comments);
    setTimeout(updateBadge, 0);
    return c;
  }
  function addReply(commentId, replyText, isAI, debugMeta = null, backendReplyId = null) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return;
    c.replies.push({
      id: Date.now(),
      backendReplyId,
      text: replyText,
      isAI,
      debugMeta,
      createdAt: new Date().toISOString(),
    });
    save(comments);
    return c;
  }
  function replaceReply(commentId, replyId, replyText, isAI, debugMeta = null, backendReplyId = null) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return null;
    const idx = c.replies.findIndex(r => String(r.id) === String(replyId));
    if (idx === -1) return addReply(commentId, replyText, isAI, debugMeta, backendReplyId);
    c.replies[idx] = {
      ...c.replies[idx],
      id: c.replies[idx].id || Date.now(),
      backendReplyId: backendReplyId || c.replies[idx].backendReplyId || null,
      text: replyText,
      isAI,
      debugMeta,
      createdAt: new Date().toISOString(),
    };
    save(comments);
    return c;
  }
  function countAIReplies(c) {
    return (c?.replies || []).filter(r => r.isAI).length;
  }
  function patchReply(commentId, replyId, patch) {
    const comments = load();
    const c = comments.find(x => String(x.id) === String(commentId));
    if (!c) return null;
    const r = (c.replies || []).find(x => String(x.id) === String(replyId));
    if (!r) return null;
    Object.assign(r, patch || {});
    save(comments);
    updateCommentCard(commentId);
    return { comment: c, reply: r };
  }
  function getProblemReportOptions(commentId, replyId) {
    const comments = load();
    const c = comments.find(x => String(x.id) === String(commentId));
    const r = c?.replies?.find(x => String(x.id) === String(replyId));
    const current = r?.problemReportOptions || {};
    const readBox = (name, fallback) => {
      const el = document.getElementById(`kb-report-${name}-${commentId}-${replyId}`);
      return el ? Boolean(el.checked) : Boolean(fallback);
    };
    return {
      include_conversation: readBox("conversation", current.include_conversation ?? true),
      include_selection: readBox("selection", current.include_selection ?? true),
      include_page_info: readBox("page", current.include_page_info ?? true),
      include_model_io: readBox("model", current.include_model_io ?? true),
    };
  }

  function problemReportPayload(commentId, replyId, rating, userNote = "") {
    const comments = load();
    const c = comments.find(x => String(x.id) === String(commentId));
    const r = c?.replies?.find(x => String(x.id) === String(replyId));
    if (!c?.agentCommentId) throw new Error("这条评论还没有后端记录，无法生成诊断包");
    return {
      comment_id: Number(c.agentCommentId),
      reply_id: r?.backendReplyId ? Number(r.backendReplyId) : null,
      rating: rating || "",
      user_note: userNote || "",
      ...getProblemReportOptions(commentId, replyId),
      client_context: getTelemetryEnv(),
    };
  }

  async function refreshProblemReportPreview(commentId, replyId, rating = "") {
    try {
      patchReply(commentId, replyId, { problemReportPreviewLoading: true, problemReportPreviewError: "" });
      const comments = load();
      const c = comments.find(x => String(x.id) === String(commentId));
      const r = c?.replies?.find(x => String(x.id) === String(replyId));
      const note = document.getElementById(`kb-feedback-ta-${commentId}-${replyId}`)?.value || r?.problemReportNote || "";
      const resp = await fetch("http://localhost:8766/debug/problem-reports/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(problemReportPayload(commentId, replyId, rating || r?.problemReportRating || "", note)),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      patchReply(commentId, replyId, {
        problemReportPreview: data.preview || {},
        problemReportOptions: getProblemReportOptions(commentId, replyId),
        problemReportNote: note,
        problemReportPreviewLoading: false,
        problemReportPreviewError: "",
      });
      return data;
    } catch (e) {
      patchReply(commentId, replyId, {
        problemReportPreviewLoading: false,
        problemReportPreviewError: e.message || String(e),
      });
      return null;
    }
  }

  async function openProblemReportPanel(commentId, replyId, options = {}) {
    patchReply(commentId, replyId, {
      feedbackDraftOpen: true,
      positiveExamplePromptOpen: false,
      positiveExampleDraftOpen: Boolean(options.positiveExample),
      problemReportOnly: Boolean(options.reportOnly),
      problemReportRating: options.rating || (options.positiveExample ? "positive_example" : (options.reportOnly ? "problem" : "down")),
      problemReportOptions: {
        include_conversation: true,
        include_selection: true,
        include_page_info: true,
        include_model_io: true,
      },
      problemReportPreview: null,
      problemReportPreviewError: "",
    });
    await refreshProblemReportPreview(commentId, replyId, options.rating || (options.reportOnly ? "problem" : "down"));
  }

  async function submitProblemReport(commentId, replyId, rating, userNote = "") {
    const payload = {
      ...problemReportPayload(commentId, replyId, rating, userNote),
      confirm_consent: true,
    };
    const resp = await fetch("http://localhost:8766/debug/problem-reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  }

  async function submitAiFeedback(commentId, replyId, rating, feedbackText = "") {
    const comments = load();
    const c = comments.find(x => String(x.id) === String(commentId));
    const r = c?.replies?.find(x => String(x.id) === String(replyId));
    if (!c || !r) return;
    if (rating === "down" && !feedbackText.trim()) {
      showToast("写一句哪里不对，再发送反馈", "error");
      return;
    }
    let report = null;
    if (rating === "down") {
      try {
        report = await submitProblemReport(commentId, replyId, rating, feedbackText.trim());
      } catch (e) {
        showToast("诊断包暂时没发出：本地服务未连接", "error");
        return;
      }
    }
    // 字段瘦身：stage/role/model/token/cost 都查 ledger，前端事件只携带"用户视角"
    await telemetryEvent("ai_reply_feedback_submitted", "sidebar", {
      rating,
      feedback_text: rating === "down" ? feedbackText.trim() : "",
      reply_chars_bucket: charBucket(r.text),
      support_report_id: report?.report_id || "",
    }, c);
    patchReply(commentId, replyId, {
      feedbackRating: rating,
      feedbackSubmitted: true,
      feedbackDraftOpen: false,
      positiveExamplePromptOpen: rating === "up" && !r.positiveExampleSubmitted && !r.positiveExampleDismissed,
      problemReportSubmitted: Boolean(report),
      problemReportId: report?.report_id || "",
    });
    showToast(rating === "down" ? "反馈和诊断包已记录" : "反馈已记录", "success");
  }

  async function submitProblemReportOnly(commentId, replyId, note = "") {
    try {
      const report = await submitProblemReport(commentId, replyId, "problem", note.trim());
      patchReply(commentId, replyId, {
        feedbackDraftOpen: false,
        problemReportOnly: false,
        problemReportSubmitted: true,
        problemReportId: report?.report_id || "",
      });
      showToast("问题报告已记录", "success");
    } catch (e) {
      showToast("问题报告暂时没发出：本地服务未连接", "error");
    }
  }

  async function submitPositiveExampleReport(commentId, replyId, note = "") {
    try {
      const report = await submitProblemReport(commentId, replyId, "positive_example", note.trim());
      patchReply(commentId, replyId, {
        feedbackDraftOpen: false,
        positiveExampleDraftOpen: false,
        positiveExamplePromptOpen: false,
        positiveExampleSubmitted: true,
        problemReportSubmitted: true,
        problemReportId: report?.report_id || "",
      });
      showToast("好例子已发送", "success");
    } catch (e) {
      showToast("好例子暂时没发出：本地服务未连接", "error");
    }
  }
  function setAiPending(commentId, patch = {}) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) return null;
    c.aiPending = {
      startedAt: new Date().toISOString(),
      existingAIReplyCount: countAIReplies(c),
      ...(c.aiPending || {}),
      ...patch,
    };
    save(comments);
    return c;
  }
  function clearAiPending(commentId) {
    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c || !c.aiPending) return null;
    delete c.aiPending;
    save(comments);
    return c;
  }

  function _backendReplyToLocal(reply) {
    const author = String(reply.author || "").toLowerCase();
    const isAI = author !== "user" && author !== "me" && author !== "human" && author !== "你";
    return {
      id: reply.id || Date.now(),
      text: reply.content || "",
      isAI,
      debugMeta: reply.debug_meta || null,
      createdAt: reply.created_at || new Date().toISOString(),
    };
  }

  function _backendCommentToLocal(row) {
    return {
      id: Number(row.id) || Date.now(),
      agentCommentId: Number(row.id) || null,
      notionPageId: row.notion_page_id || null,
      telemetryThreadId: row.thread_telemetry_id || null,
      excerpt: row.selected_text || "",
      text: row.comment || "",
      createdAt: row.created_at || new Date().toISOString(),
      replies: Array.isArray(row.replies) ? row.replies.map(_backendReplyToLocal) : [],
    };
  }

  function _mergeBackendComments(rows) {
    if (!Array.isArray(rows) || !rows.length) return false;
    const comments = load();
    let changed = false;
    for (const row of rows) {
      const backendId = Number(row.id);
      if (!backendId) continue;
      const existing = comments.find(c => Number(c.agentCommentId) === backendId || Number(c.id) === backendId);
      const next = _backendCommentToLocal(row);
      if (existing) {
        Object.assign(existing, {
          agentCommentId: next.agentCommentId,
          notionPageId: next.notionPageId || existing.notionPageId || null,
          excerpt: next.excerpt || existing.excerpt || "",
          text: next.text || existing.text || "",
          createdAt: existing.createdAt || next.createdAt,
          replies: next.replies.length ? next.replies : (existing.replies || []),
        });
      } else {
        comments.unshift(next);
      }
      changed = true;
    }
    if (changed) save(comments);
    return changed;
  }

  let _backendPageCommentsHydrated = false;
  async function hydratePageCommentsFromBackend() {
    if (_backendPageCommentsHydrated) return;
    _backendPageCommentsHydrated = true;
    let changed = false;
    for (const url of _pageUrlCandidates()) {
      try {
        const resp = await fetch(`http://localhost:8766/comments?page_url=${encodeURIComponent(url)}`);
        if (!resp.ok) continue;
        const rows = await resp.json();
        if (_mergeBackendComments(rows)) changed = true;
      } catch {
        // 后端离线时不影响本页评注入口；本地 localStorage 仍可用。
      }
    }
    if (!changed) return;
    restoreCommentHighlights();
    if (!panelEl) buildPanel({ hidden: !panelOpen });
    render();
    updateBadge();
  }

  // ── highlights 持久化存储（独立于 comments）──
  const HL_KEY = () => "kb_highlights_" + _storageUrl();
  function _highlightMergeKey(h) {
    return `${h?.excerpt || ""}:${JSON.stringify(h?.position || {})}`;
  }
  function loadHighlights() {
    const seen = new Set();
    const merged = [];
    for (const key of _storageKeys("kb_highlights_")) {
      let items = [];
      try { items = JSON.parse(localStorage.getItem(key) || "[]"); } catch { items = []; }
      if (!Array.isArray(items)) continue;
      for (const item of items) {
        const mergeKey = _highlightMergeKey(item);
        if (seen.has(mergeKey)) continue;
        seen.add(mergeKey);
        merged.push(item);
      }
    }
    return merged;
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

  function _acceptedTextNodes() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (parent.closest("#kb-comment-panel, #kb-toast, script, style, textarea")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    let node;
    while ((node = walker.nextNode())) {
      if (node.textContent) nodes.push(node);
    }
    return nodes;
  }

  function _normalizeForAnchor(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function _compactForAnchor(value) {
    return String(value || "").replace(/\s+/g, "").trim();
  }

  function _findExactTextRange(excerpt) {
    for (const node of _acceptedTextNodes()) {
      const idx = node.textContent.indexOf(excerpt);
      if (idx === -1) continue;
      try {
        const range = document.createRange();
        range.setStart(node, idx);
        range.setEnd(node, idx + excerpt.length);
        return range;
      } catch {
        return null;
      }
    }
    return null;
  }

  function _findTextRangeAcrossNodes(excerpt, opts = {}) {
    const compactWhitespace = Boolean(opts.compactWhitespace);
    const normalize = compactWhitespace ? _compactForAnchor : _normalizeForAnchor;
    const needle = normalize(excerpt);
    if (!needle) return null;
    const nodes = _acceptedTextNodes();
    let haystack = "";
    const index = [];
    for (const node of nodes) {
      for (let i = 0; i < node.textContent.length; i++) {
        const raw = node.textContent[i];
        if (compactWhitespace && /\s/.test(raw)) continue;
        const normalized = /\s/.test(raw) ? " " : raw;
        if (normalized === " " && haystack.endsWith(" ")) continue;
        haystack += normalized;
        index.push({ node, offset: i });
      }
    }
    const start = haystack.indexOf(needle);
    if (start === -1) return null;
    const end = start + needle.length - 1;
    const startRef = index[start];
    const endRef = index[end];
    if (!startRef || !endRef) return null;
    const range = document.createRange();
    range.setStart(startRef.node, startRef.offset);
    range.setEnd(endRef.node, endRef.offset + 1);
    if (normalize(range.toString()) !== needle) return null;
    return range;
  }

  function _markGroupText(excerpt) {
    return _findMarksForExcerpt(excerpt).map(m => m.textContent || "").join(" ");
  }

  function _markGroupLooksLikeExcerpt(excerpt) {
    const needle = _normalizeForAnchor(excerpt);
    const text = _normalizeForAnchor(_markGroupText(excerpt));
    if (!needle || !text) return false;
    if (text === needle) return true;
    return text.includes(needle) && text.length <= needle.length * 1.25 + 24;
  }

  function _removeMarksForExcerpt(excerpt) {
    _findMarksForExcerpt(excerpt).forEach(mark => {
      const parent = mark.parentNode;
      if (!parent) return;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      try { parent.normalize(); } catch {}
    });
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
        _showPanel();
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
    mark.style.background = "oklch(0.90 0.07 150 / 0.62)";
    mark.style.color = "oklch(0.18 0.012 145)";
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
    try {
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
    } catch (err) {
      console.warn("[KB] highlight failed, will continue without blocking panel:", err);
      return false;
    }
  }

  // ── 高亮（由小bar或右键菜单触发，range 存在）──
  function doHighlight(excerpt, range) {
    if (!range) {
      highlightByText(excerpt);
      return _findMarksForExcerpt(excerpt).length > 0;
    }
    const position = serializeRange(range);
    let ok = insertMark(range, excerpt); // 先插 mark，再清 selection
    try { window.getSelection()?.removeAllRanges(); } catch {}
    // 如果 range 方式失败（跨节点等），fallback 到文字匹配
    if (!ok) {
      highlightByText(excerpt);
      ok = _findMarksForExcerpt(excerpt).length > 0;
    }
    if (ok && position) {
      addHighlight(excerpt, position);
    }
    return ok;
  }

  // ── 高亮 + 打开评论面板（点"评论"按钮）──
  function doHighlightAndOpenComment(excerpt, range) {
    doHighlight(excerpt, range);
    open(excerpt, location.href, document.title);
  }

  // ── 右键菜单触发：selection 已消失，用文字内容在页面上匹配并高亮 ──
  function highlightByText(excerpt) {
    if (!excerpt) return;
    const exactRange = _findExactTextRange(excerpt);
    if (exactRange) {
      const position = serializeRange(exactRange);
      if (insertMark(exactRange, excerpt) && position) addHighlight(excerpt, position);
      return;
    }
    const crossNodeRange = _findTextRangeAcrossNodes(excerpt);
    if (crossNodeRange) {
      const position = serializeRange(crossNodeRange);
      if (insertMark(crossNodeRange, excerpt) && position) addHighlight(excerpt, position);
      return;
    }
    const compactCrossNodeRange = _findTextRangeAcrossNodes(excerpt, { compactWhitespace: true });
    if (compactCrossNodeRange) {
      const position = serializeRange(compactCrossNodeRange);
      if (insertMark(compactCrossNodeRange, excerpt) && position) addHighlight(excerpt, position);
      return;
    }
  }

  // ── 页面加载时恢复所有高亮 ──
  function restoreHighlights() {
    const hls = loadHighlights();
    hls.forEach(h => {
      if (!h.position) return;
      if (_findMarksForExcerpt(h.excerpt).length) return;
      const range = deserializeRange(h.position);
      if (!range) return;
      if (!insertMark(range, h.excerpt)) return;
      if (!_markGroupLooksLikeExcerpt(h.excerpt)) {
        _removeMarksForExcerpt(h.excerpt);
        highlightByText(h.excerpt);
      }
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

  function captureSavedToast(resp, baseText) {
    const notionState = resp?.externalSync?.notion || resp?.notionStatus || "";
    if (notionState === "synced" || notionState === "notion_synced") {
      return `✓ ${baseText}，Notion 备份已同步`;
    }
    if (notionState === "failed" || notionState === "notion_failed") {
      return `✓ ${baseText}（Notion 备份失败，可稍后重试）`;
    }
    return `✓ ${baseText}`;
  }

  // ── 高亮后静默保存到本地记忆库；外部备份由后端按配置处理 ──
  function saveHighlightToVault(excerpt, title, url, platform) {
    fetch("http://localhost:8766/captures/save", {
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
        console.error("[KB] capture save HTTP error:", r.status, r.statusText);
        return r.text().then(t => { throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`); });
      }
      return r.json();
    }).then(resp => {
      if (resp.success) {
        if (resp.notionSynced === false && resp.notionError) console.warn("[KB] optional Notion backup not synced:", resp.notionError);
        telemetryEvent("highlight_saved", "selection_bar", {
          selected_text_chars_bucket: charBucket(excerpt),
        });
        showToast(captureSavedToast(resp, "已高亮并保存到本地记忆库"), "success");
      } else {
        const detail = resp.detail || "未知错误";
        console.error("[KB] capture save failed:", detail);
        showToast("✗ 本地保存失败：" + detail, "error");
      }
    }).catch(err => {
      console.error("[KB] capture save error:", err.message || err);
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
      background: oklch(0.55 0.14 150); color: oklch(0.985 0.006 125);
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
    if (!panelOpen) {
      _showPanel();
      render();
      syncVisibleCommentsFromBackend({ notify: false });
    } else {
      _hidePanel();
    }
  }

  // ── 注入样式（v3 视觉系统：森林绿品牌主色 + 浅纸面 + JetBrains Mono + oklch）──
  function injectStyles() {
    if (document.getElementById("kb-comment-style")) return;
    const s = document.createElement("style");
    s.id = "kb-comment-style";
    s.textContent = `
      :root {
        --kb-paper: oklch(0.985 0.006 125);
        --kb-paper-2: oklch(0.965 0.014 135);
        --kb-paper-3: oklch(0.94 0.018 135);
        --kb-surface: oklch(0.995 0.003 125);
        --kb-ink: oklch(0.18 0.012 145);
        --kb-ink-2: oklch(0.32 0.014 145);
        --kb-ink-mute: oklch(0.54 0.02 145);
        --kb-ink-faint: oklch(0.72 0.016 145);
        --kb-line: oklch(0.85 0.016 135);
        --kb-line-2: oklch(0.91 0.012 135);
        --kb-brand: oklch(0.55 0.14 150);
        --kb-brand-strong: oklch(0.42 0.13 150);
        --kb-brand-soft: oklch(0.96 0.02 150);
        --kb-brand-faint: oklch(0.975 0.014 150);
        --kb-notice-bg: oklch(0.985 0.018 145 / 0.98);
        --kb-notice-text: oklch(0.20 0.014 145);
        --kb-notice-border: oklch(0.55 0.14 150 / 0.32);
        --kb-notice-action-bg: oklch(0.42 0.13 150);
        --kb-notice-action-text: oklch(0.985 0.006 125);
        --kb-panel-shadow: rgba(16,44,32,0.08);
        --kb-card-shadow: rgba(60,40,20,0.04);
        --kb-grid-dot: oklch(0.91 0.014 145);
        --kb-reply-user-bg: oklch(0.96 0.02 150);
        --kb-reply-user-border: oklch(0.86 0.045 150);
        --kb-reply-ai-bg: oklch(0.985 0.004 145);
        --kb-reply-ai-accent: oklch(0.62 0.11 150);
        --kb-terra: var(--kb-brand);
        --kb-terra-soft: var(--kb-brand-soft);
        --kb-blue: var(--kb-brand);
        --kb-blue-soft: var(--kb-brand-soft);
        --kb-highlight: oklch(0.90 0.07 150 / 0.62);
        --kb-highlight-strong: oklch(0.82 0.10 150 / 0.78);
        --kb-highlight-text: oklch(0.18 0.012 145);
        --kb-highlight-ring: oklch(0.55 0.14 150 / 0.28);
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --kb-paper: oklch(0.18 0.012 145);
          --kb-paper-2: oklch(0.22 0.014 145);
          --kb-paper-3: oklch(0.27 0.016 145);
          --kb-surface: oklch(0.205 0.014 145);
          --kb-ink: oklch(0.94 0.008 135);
          --kb-ink-2: oklch(0.84 0.01 135);
          --kb-ink-mute: oklch(0.68 0.014 135);
          --kb-ink-faint: oklch(0.52 0.014 135);
          --kb-line: oklch(0.36 0.018 145);
          --kb-line-2: oklch(0.29 0.016 145);
          --kb-brand: oklch(0.70 0.12 150);
          --kb-brand-strong: oklch(0.55 0.14 150);
          --kb-brand-soft: oklch(0.28 0.035 150);
          --kb-brand-faint: oklch(0.235 0.024 150);
          --kb-notice-bg: oklch(0.245 0.026 150 / 0.98);
          --kb-notice-text: oklch(0.94 0.008 135);
          --kb-notice-border: oklch(0.64 0.11 150 / 0.36);
          --kb-notice-action-bg: oklch(0.70 0.12 150);
          --kb-notice-action-text: oklch(0.12 0.014 145);
          --kb-panel-shadow: rgba(0,0,0,0.34);
          --kb-card-shadow: rgba(0,0,0,0.18);
          --kb-grid-dot: oklch(0.30 0.014 145);
          --kb-reply-user-bg: oklch(0.245 0.026 150);
          --kb-reply-user-border: oklch(0.40 0.052 150);
          --kb-reply-ai-bg: oklch(0.215 0.012 145);
          --kb-reply-ai-accent: oklch(0.64 0.11 150);
          --kb-highlight: oklch(0.48 0.09 150 / 0.64);
          --kb-highlight-strong: oklch(0.58 0.12 150 / 0.78);
          --kb-highlight-text: oklch(0.96 0.008 135);
          --kb-highlight-ring: oklch(0.70 0.12 150 / 0.32);
        }
      }
      @keyframes kb-card-flash {
        0%   { box-shadow: 0 1px 2px rgba(60,40,20,0.05); border-color: var(--kb-line); }
        25%  { box-shadow: 0 0 0 3px oklch(0.55 0.14 150 / 0.18); border-color: var(--kb-terra); }
        100% { box-shadow: 0 1px 2px rgba(60,40,20,0.05); border-color: var(--kb-line); }
      }
      mark.kb-comment-highlight {
        background: var(--kb-highlight) !important;
        color: var(--kb-highlight-text) !important;
        border-radius: 2px;
        cursor: pointer;
        padding: 1px 1px;
        transition: background 0.2s, box-shadow 0.2s;
        box-shadow: inset 0 -0.12em 0 var(--kb-highlight-ring);
        box-decoration-break: clone;
        -webkit-box-decoration-break: clone;
      }
      mark.kb-comment-highlight.kb-mark-active {
        background: var(--kb-highlight-strong) !important;
      }
      @keyframes kb-mark-pulse-anim {
        0%   { background: var(--kb-highlight) !important; box-shadow: 0 0 0 0 oklch(0.55 0.14 150 / 0.0); }
        30%  { background: var(--kb-highlight-strong) !important; box-shadow: 0 0 0 4px var(--kb-highlight-ring); }
        100% { background: var(--kb-highlight) !important; box-shadow: inset 0 -0.12em 0 var(--kb-highlight-ring), 0 0 0 0 oklch(0.55 0.14 150 / 0.0); }
      }
      mark.kb-comment-highlight.kb-mark-pulse {
        animation: kb-mark-pulse-anim 0.85s ease-in-out 1;
      }
      .kb-cmt-card.kb-flash {
        animation: kb-card-flash 0.7s ease-in-out 1;
      }
      .kb-cmt-card.kb-anchor {
        border-color: var(--kb-terra) !important;
        box-shadow: 0 0 0 3px oklch(0.55 0.14 150 / 0.14) !important;
      }
      html.kb-panel-resizing,
      html.kb-panel-resizing * {
        cursor: ew-resize !important;
        user-select: none !important;
      }
      #kb-comment-panel {
        position: fixed; top: 0; right: 0; width: var(--kb-panel-width, 380px); height: 100vh;
        min-width: 320px; max-width: calc(100vw - 72px);
        background: var(--kb-paper);
        border-left: 1px solid var(--kb-line);
        display: flex; flex-direction: column; z-index: 2147483645;
        font-family: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
        font-size: 14px; color: var(--kb-ink);
        box-shadow: -8px 0 28px var(--kb-panel-shadow);
        transform: translateX(0); transition: transform 0.28s ease;
        overflow: hidden;
        background-image:
          radial-gradient(var(--kb-grid-dot) 0.5px, transparent 0.5px);
        background-size: 20px 20px;
      }
      #kb-panel-resizer {
        position: absolute !important;
        left: 0 !important;
        right: auto !important;
        top: 0 !important;
        width: 12px !important;
        min-width: 12px !important;
        max-width: 12px !important;
        height: 100% !important;
        cursor: ew-resize !important;
        z-index: 2 !important;
        touch-action: none !important;
        flex: 0 0 12px !important;
        box-sizing: border-box !important;
      }
      #kb-panel-resizer::after {
        content: "";
        position: absolute;
        left: 1px;
        top: 14px;
        bottom: 14px;
        width: 2px;
        border-radius: 999px;
        background: transparent;
        transition: background 0.16s;
      }
      #kb-panel-resizer:hover::after,
      html.kb-panel-resizing #kb-panel-resizer::after {
        background: var(--kb-brand);
      }
      #kb-comment-panel button {
        -webkit-appearance: none !important;
        appearance: none !important;
        text-transform: none !important;
        box-sizing: border-box !important;
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
        background: transparent !important; border: 0 !important; padding: 4px 8px !important;
        font-size: 12px !important; line-height: 1.2 !important; cursor: pointer; color: var(--kb-ink-mute) !important;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-close:hover { color: var(--kb-terra) !important; }
      #kb-cp-body {
        flex: 1; overflow-y: auto; padding: 12px 14px;
        position: relative;
      }
      .kb-cmt-card {
        background: var(--kb-surface);
        border: 1px solid var(--kb-line-2);
        border-radius: 6px;
        padding: 12px 14px 10px;
        box-shadow: 0 1px 2px var(--kb-card-shadow);
        margin-bottom: 10px;
        transition: border-color 0.18s, box-shadow 0.18s;
      }
      .kb-cmt-card:hover { border-color: var(--kb-line); }
      .kb-cmt-card.kb-ai-unread {
        border-color: var(--kb-blue);
        box-shadow: 0 0 0 2px oklch(0.55 0.14 150 / 0.10);
      }
      .kb-cmt-content {
        max-height: 320px; overflow: hidden; position: relative;
        transition: max-height 0.3s ease;
      }
      .kb-cmt-content.expanded { max-height: none; overflow: visible; }
      .kb-cmt-content.overflowing:not(.expanded)::after {
        content: ''; position: absolute; bottom: 0; left: 0; right: 0;
        height: 36px;
        background: linear-gradient(transparent, var(--kb-surface));
        pointer-events: none;
      }
      .kb-cmt-expand {
        font-size: 11px !important; line-height: 1.25 !important; color: var(--kb-terra) !important; cursor: pointer; margin-top: 4px;
        background: transparent !important; border: 0 !important; padding: 0 !important; text-align: left;
        font-family: inherit; letter-spacing: 0.03em;
      }
      .kb-cmt-expand:hover { text-decoration: underline; color: var(--kb-terra) !important; }
      .kb-cmt-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
        align-items: center;
        flex-wrap: wrap;
        position: relative;
        z-index: 1;
      }
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
        position: relative;
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 8px 10px; font-size: 13px;
        line-height: 1.55; margin-bottom: 6px;
      }
      .kb-reply.ai {
        background: var(--kb-reply-ai-bg);
        border-color: var(--kb-line-2);
        color: var(--kb-ink);
      }
      .kb-reply.ai::before {
        content: '';
        position: absolute;
        top: -1px;
        left: 10px;
        width: 34px;
        height: 2px;
        border-radius: 999px;
        background: var(--kb-reply-ai-accent);
      }
      .kb-reply.user {
        background: var(--kb-reply-user-bg);
        border-color: var(--kb-reply-user-border);
        color: var(--kb-ink);
      }
      .kb-reply-label {
        font-size: 10px; color: var(--kb-ink-mute); margin-bottom: 4px;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        font-weight: 500; letter-spacing: 0.06em;
      }
      .kb-reply-label .kb-reply-tag {
        display: inline-block;
        background: var(--kb-brand-strong); color: white;
        font-size: 9px; padding: 1px 5px; border-radius: 2px;
        margin-right: 6px; letter-spacing: 0.08em; font-weight: 600;
      }
      .kb-reply-body { font-size: 13px; line-height: 1.6; margin: 0; }
      .kb-reply.ai .kb-reply-body > p:first-child { margin-top: 0; }
      .kb-reply.ai .kb-reply-body p { margin: 0 0 4px 0; font-size: 13px; line-height: 1.6; }
      .kb-reply.ai .kb-reply-body p:last-child { margin-bottom: 0; }
      .kb-reply.user .kb-reply-body { white-space: pre-wrap; }
      .kb-reply.ai .kb-reply-body ul, .kb-reply.ai .kb-reply-body ol { margin: 3px 0 5px 0; padding-left: 16px; }
      .kb-reply.ai .kb-reply-body li { margin-bottom: 2px; font-size: 13px; line-height: 1.6; }
      .kb-reply.ai .kb-reply-body h1, .kb-reply.ai .kb-reply-body h2 { font-size: 13px; font-weight: 600; margin: 6px 0 3px; }
      .kb-reply.ai .kb-reply-body h3, .kb-reply.ai .kb-reply-body h4 { font-size: 13px; font-weight: 600; margin: 5px 0 3px; }
      .kb-reply.ai .kb-reply-body strong { font-weight: 600; }
      .kb-reply.ai .kb-reply-body em { font-style: italic; }
      .kb-reply.ai .kb-reply-body code {
        font-family: "JetBrains Mono", ui-monospace, monospace;
        background: var(--kb-paper-2); border: 1px solid var(--kb-line-2);
        padding: 1px 4px; border-radius: 2px; font-size: 11px;
      }
      .kb-reply.ai .kb-reply-body pre {
        background: var(--kb-paper-2); border: 1px solid var(--kb-line-2);
        padding: 8px 10px; border-radius: 4px; overflow-x: auto; margin: 5px 0;
        font-family: "JetBrains Mono", ui-monospace, monospace;
      }
      .kb-reply.ai .kb-reply-body table { border-collapse: collapse; width: 100%; font-size: 11px; margin: 5px 0; }
      .kb-reply.ai .kb-reply-body th, .kb-reply.ai .kb-reply-body td { border: 1px solid var(--kb-line); padding: 3px 6px; text-align: left; }
      .kb-reply.ai .kb-reply-body th { background: var(--kb-paper-2); font-weight: 600; }
      .kb-reply.ai .kb-reply-body blockquote { border-left: 2px solid var(--kb-line); padding-left: 8px; margin: 4px 0; color: var(--kb-ink-mute); }
      .kb-reply.ai .kb-reply-body a { color: var(--kb-blue); text-decoration: none; border-bottom: 1px solid oklch(0.55 0.14 150 / 0.4); }
      .kb-reply.ai .kb-reply-body a:hover { border-bottom-color: var(--kb-blue); }
      .kb-reply-actions { margin-top: 8px; }
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
      #kb-comment-panel .kb-reply-send {
        background: var(--kb-brand-strong) !important; color: var(--kb-paper) !important;
        border: 0 !important; border-radius: 3px; padding: 5px 12px !important;
        font-size: 11px !important; line-height: 1.2 !important; cursor: pointer; letter-spacing: 0.04em;
        font-family: inherit;
      }
      #kb-comment-panel .kb-reply-send:hover { background: var(--kb-brand) !important; }
      #kb-comment-panel .kb-reply-btn {
        background: transparent !important; border: 0 !important;
        color: var(--kb-ink-mute) !important; font-size: 11px !important;
        line-height: 1.25 !important; cursor: pointer; padding: 0 !important; font-family: inherit;
      }
      #kb-comment-panel .kb-reply-btn:hover { color: var(--kb-terra) !important; }
      .kb-ai-feedback {
        display: flex; align-items: center; gap: 6px;
        margin-top: 8px; flex-wrap: wrap;
      }
      #kb-comment-panel .kb-feedback-chip {
        background: transparent !important; color: var(--kb-ink-mute) !important;
        border: 1px solid var(--kb-line) !important; border-radius: 3px;
        padding: 3px 7px !important; font-size: 11px !important; cursor: pointer;
        font-family: inherit; line-height: 1.2;
      }
      #kb-comment-panel .kb-feedback-chip:hover,
      #kb-comment-panel .kb-feedback-chip.active {
        border-color: var(--kb-brand-strong) !important;
        color: var(--kb-brand-strong) !important;
        background: var(--kb-brand-soft) !important;
      }
      .kb-feedback-done {
        color: var(--kb-ink-mute); font-size: 11px;
      }
      #kb-comment-panel .kb-share-btn {
        background: var(--kb-brand-faint) !important;
        color: var(--kb-brand-strong) !important;
        border: 1px solid oklch(0.55 0.14 150 / 0.24) !important;
        border-radius: 3px;
        padding: 3px 7px !important;
        font-size: 11px !important;
        line-height: 1.2 !important;
        cursor: pointer;
        font-family: inherit;
      }
      #kb-comment-panel .kb-share-btn:hover {
        border-color: var(--kb-brand) !important;
        background: var(--kb-brand-soft) !important;
      }
      .kb-positive-example-prompt {
        margin-top: 6px;
        padding: 7px 8px;
        border: 1px solid var(--kb-line-2);
        background: var(--kb-brand-faint);
        border-radius: 4px;
        color: var(--kb-ink-2);
        font-size: 11px;
        line-height: 1.45;
      }
      .kb-positive-example-actions {
        display: flex;
        gap: 6px;
        margin-top: 6px;
        flex-wrap: wrap;
      }
      .kb-feedback-panel {
        width: 100%; margin-top: 6px;
        border: 1px solid var(--kb-line-2);
        background: var(--kb-paper-soft);
        border-radius: 4px; padding: 8px;
      }
      .kb-feedback-panel textarea {
        width: 100%; min-height: 64px; resize: vertical;
        border: 1px solid var(--kb-line); border-radius: 3px;
        box-sizing: border-box; padding: 8px;
        font-family: inherit; font-size: 12px; line-height: 1.5;
        background: var(--kb-paper); color: var(--kb-ink);
      }
      .kb-feedback-note {
        color: var(--kb-ink-mute); font-size: 11px; line-height: 1.5;
        margin-top: 6px;
      }
      .kb-report-options {
        display: grid; grid-template-columns: 1fr;
        gap: 6px; margin-top: 8px;
      }
      .kb-report-option {
        position: relative;
        display: grid; grid-template-columns: 16px 1fr; gap: 8px;
        align-items: start;
        padding: 7px 8px;
        border: 1px solid var(--kb-line-2);
        border-radius: 4px;
        background: var(--kb-paper);
        cursor: pointer;
      }
      .kb-report-option:hover {
        border-color: var(--kb-line);
        background: var(--kb-brand-faint);
      }
      .kb-report-option input {
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }
      .kb-report-check {
        width: 14px; height: 14px;
        border: 1px solid var(--kb-line);
        border-radius: 3px;
        background: var(--kb-surface);
        box-sizing: border-box;
        margin-top: 1px;
        position: relative;
      }
      .kb-report-option input:checked + .kb-report-check {
        background: var(--kb-brand-strong);
        border-color: var(--kb-brand-strong);
      }
      .kb-report-option input:checked + .kb-report-check::after {
        content: "";
        position: absolute;
        left: 4px; top: 1px;
        width: 4px; height: 8px;
        border: solid var(--kb-paper);
        border-width: 0 2px 2px 0;
        transform: rotate(45deg);
      }
      .kb-report-copy {
        min-width: 0;
      }
      .kb-report-title {
        display: block;
        color: var(--kb-ink);
        font-size: 11px; line-height: 1.35;
      }
      .kb-report-desc {
        display: block;
        color: var(--kb-ink-mute);
        font-size: 10px; line-height: 1.4;
        margin-top: 2px;
      }
      .kb-report-preview {
        margin-top: 8px; padding: 7px 8px;
        border: 1px solid var(--kb-line);
        background: var(--kb-paper);
        border-radius: 3px;
        color: var(--kb-ink-2);
        font-size: 11px; line-height: 1.45;
      }
      .kb-report-preview b {
        color: var(--kb-ink);
        font-weight: 500;
      }
      .kb-report-preview ul {
        margin: 4px 0 0 16px;
        padding: 0;
      }
      .kb-report-preview li {
        margin: 2px 0;
      }
      .kb-report-preview.warn {
        color: #a40;
        border-color: color-mix(in srgb, #a40 28%, var(--kb-line));
      }
      .kb-feedback-actions {
        display: flex; gap: 8px; margin-top: 7px; align-items: center;
        flex-wrap: wrap;
      }
      #kb-comment-panel .kb-ai-btn {
        background: var(--kb-brand-strong) !important; color: var(--kb-paper) !important;
        border: 0 !important; border-radius: 3px;
        padding: 6px 14px !important; font-size: 11px !important; line-height: 1.2 !important; cursor: pointer; margin-top: 6px;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-comment-panel .kb-ai-btn:hover { background: var(--kb-brand) !important; }
      #kb-comment-panel .kb-ai-btn:disabled { background: var(--kb-ink-faint) !important; cursor: not-allowed; }
      #kb-comment-panel .kb-ai-ready-btn {
        border: 1px solid oklch(0.55 0.14 150 / 0.42) !important;
        background: var(--kb-blue-soft) !important;
        color: var(--kb-blue) !important;
        border-radius: 3px;
        padding: 4px 9px !important;
        font-size: 11px !important;
        line-height: 1.2 !important;
        cursor: pointer;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        letter-spacing: 0.03em;
      }
      #kb-comment-panel .kb-ai-ready-btn:hover {
        border-color: var(--kb-blue) !important;
        background: var(--kb-brand-soft) !important;
      }
      .kb-thinking {
        font-size: 12px; color: var(--kb-ink-2); padding: 8px 10px;
        background: var(--kb-reply-ai-bg); border-radius: 4px; margin-top: 6px;
        line-height: 1.6; border: 1px solid var(--kb-line-2);
        box-shadow: inset 0 2px 0 var(--kb-reply-ai-accent);
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
        background: transparent !important; border: 1px dashed var(--kb-line) !important;
        color: var(--kb-ink-mute) !important; padding: 4px 10px !important;
        border-radius: 3px; font-size: 11px !important; line-height: 1.2 !important; cursor: pointer;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-new-btn:hover { border-color: var(--kb-terra) !important; color: var(--kb-terra) !important; border-style: solid !important; }
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
        margin-top: 8px; background: var(--kb-brand-strong) !important; color: var(--kb-paper) !important;
        border: 0 !important; border-radius: 3px;
        padding: 7px 18px !important; font-size: 12px !important; line-height: 1.2 !important; cursor: pointer;
        font-family: inherit; letter-spacing: 0.04em;
      }
      #kb-cp-send-btn:hover { background: var(--kb-brand) !important; }
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
        border: 1px solid var(--kb-notice-border);
        border-radius: 6px;
        background: var(--kb-notice-bg);
        color: var(--kb-notice-text) !important;
        box-shadow: 0 4px 16px rgba(16, 44, 32, 0.10);
        font-size: 12px;
        font-family: "Source Han Serif SC", "Noto Serif SC", serif;
        line-height: 1.35;
      }
      #kb-cp-ai-notice.kb-hidden { display: none; }
      #kb-cp-ai-notice button {
        -webkit-appearance: none !important;
        appearance: none !important;
        border: 0 !important;
        background: var(--kb-notice-action-bg) !important;
        color: var(--kb-notice-action-text) !important;
        border-radius: 3px;
        padding: 4px 9px !important;
        font-size: 11px !important;
        line-height: 1.2 !important;
        cursor: pointer;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        text-transform: none !important;
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
      #kb-share-dialog {
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
        background: rgba(18, 24, 20, 0.34);
        box-sizing: border-box;
        font-family: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      }
      .kb-share-shell {
        width: min(640px, calc(100vw - 32px));
        max-height: calc(100vh - 40px);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        border: 0;
        border-radius: 16px;
        background: #fff;
        color: var(--kb-ink);
        box-shadow: 0 24px 60px rgba(0,0,0,0.18), 0 4px 12px rgba(0,0,0,0.06);
      }
      .kb-share-header,
      .kb-share-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 18px 24px;
        border-bottom: 1px solid #eee;
        background: #fff;
        flex-shrink: 0;
      }
      .kb-share-actions {
        padding: 14px 24px;
        border-top: 1px solid #eee;
        border-bottom: 0;
        justify-content: flex-end;
        flex-wrap: wrap;
        background: #fafafa;
      }
      .kb-share-title {
        margin: 0;
        font-size: 16px;
        font-weight: 600;
        font-family: inherit;
      }
      .kb-share-tabs {
        display: flex;
        gap: 8px;
        padding: 0 24px;
        border-bottom: 1px solid #eee;
        background: #fff;
        flex-shrink: 0;
      }
      .kb-share-tab {
        background: transparent !important;
        border: 0 !important;
        border-bottom: 2px solid transparent !important;
        padding: 12px 4px !important;
        margin-bottom: -1px;
        font-size: 14px !important;
        line-height: 1.2 !important;
        color: #888 !important;
        font-family: inherit;
        cursor: pointer;
        border-radius: 0;
      }
      .kb-share-tab:hover {
        color: var(--kb-ink) !important;
      }
      .kb-share-tab.is-active {
        color: var(--kb-brand-strong) !important;
        border-bottom-color: var(--kb-brand-strong) !important;
        font-weight: 600;
      }
      .kb-share-preview {
        min-height: 180px;
        overflow: auto;
        padding: 24px;
        background: #d8d8d2;
        display: flex;
        justify-content: center;
        align-items: flex-start;
      }
      .kb-share-preview img {
        display: block;
        flex: 0 0 auto;
        width: 520px;
        max-width: 100%;
        height: auto;
        border-radius: 6px;
        box-shadow: 0 10px 28px rgba(20,30,22,0.10), 0 0 0 1px rgba(20,30,22,0.04);
      }
      .kb-share-loading,
      .kb-share-status {
        color: var(--kb-ink-mute);
        font-size: 12px;
        line-height: 1.5;
      }
      #kb-share-dialog .kb-share-copy {
        background: var(--kb-brand-strong) !important;
        color: var(--kb-paper) !important;
        border: 0 !important;
        border-radius: 3px;
        padding: 7px 12px !important;
        font-size: 12px !important;
        line-height: 1.2 !important;
        cursor: pointer;
        font-family: inherit;
      }
      #kb-share-dialog .kb-share-ghost {
        background: transparent !important;
        color: var(--kb-ink-mute) !important;
        border: 0 !important;
        padding: 7px 4px !important;
        font-size: 12px !important;
        line-height: 1.2 !important;
        cursor: pointer;
        font-family: inherit;
      }
      #kb-share-dialog .kb-share-ghost:hover {
        color: var(--kb-brand-strong) !important;
      }
      .kb-expand-hidden { display: none !important; }
    `;
    document.head.appendChild(s);
  }

  // ── 构建面板 ──
  function buildPanel(options = {}) {
    if (panelEl) return;
    injectStyles();
    panelEl = document.createElement("div");
    panelEl.id = "kb-comment-panel";
    if (options.hidden) {
      panelEl.classList.add("kb-btn-hidden");
    }
    panelEl.innerHTML = `
      <div id="kb-panel-resizer" title="拖动调整评注栏宽度"></div>
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
    _applyPanelWidth(panelWidth);

    document.getElementById("kb-panel-resizer").addEventListener("mousedown", _startPanelResize);
    document.getElementById("kb-panel-resizer").addEventListener("touchstart", _startPanelResize, { passive: false });
    document.getElementById("kb-cp-close").addEventListener("click", () => {
      _hidePanel();
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
      const shareBtn = e.target.closest("[data-share-reply]");
      if (shareBtn) {
        openShareDialog(shareBtn.dataset.shareReply, shareBtn.dataset.replyId);
        return;
      }
      const readyBtn = e.target.closest("[data-jump-ai]");
      if (readyBtn) {
        jumpToAiReply(parseInt(readyBtn.dataset.jumpAi, 10));
        return;
      }
      const retryBtn = e.target.closest("[data-regenerate-ai]");
      if (retryBtn) {
        askAI(parseInt(retryBtn.dataset.regenerateAi, 10), { replaceReplyId: retryBtn.dataset.replyId });
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

  function _normalizeShareText(value) {
    return String(value || "")
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function _shareDisplayText(value) {
    let text = _normalizeShareText(value);
    if (!text) return "";
    text = text
      .replace(/```[^\n]*\n([\s\S]*?)```/g, (_, code) => _normalizeShareText(code))
      .replace(/`([^`]+)`/g, "$1")
      .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/^\s{0,3}#{1,6}\s+/gm, "")
      .replace(/^\s{0,3}>\s?/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "• ")
      .replace(/^\s*(\d+)[.)]\s+/gm, "$1. ")
      .replace(/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/gm, "")
      .replace(/^\s*\|(.+)\|\s*$/gm, (_, row) => row.split("|").map(cell => cell.trim()).filter(Boolean).join(" · "))
      .replace(/\*\*([^*\n]+)\*\*/g, "$1")
      .replace(/__([^_\n]+)__/g, "$1")
      .replace(/(^|[^\*])\*([^*\n]+)\*/g, "$1$2")
      .replace(/(^|[^_])_([^_\n]+)_/g, "$1$2")
      .replace(/~~([^~\n]+)~~/g, "$1")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>\s*<p>/gi, "\n\n")
      .replace(/<[^>]+>/g, "");
    return _normalizeShareText(text);
  }

  function _shareVariantKey(variant) {
    if (variant === "min" || variant === "min_comment") return "min_comment";
    if (variant === "min_quote") return "min_quote";
    return "full";
  }

  function _shareMinSource(payload, variant) {
    const key = _shareVariantKey(variant);
    const raw = key === "min_quote" ? payload.excerpt : (payload.userText || payload.excerpt);
    const text = _shareDisplayText(raw);
    return text ? `“${text}”` : "";
  }

  function _roundRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function _wrapCanvasText(ctx, text, maxWidth, maxLines = Infinity) {
    const paragraphs = _normalizeShareText(text).split(/\n{2,}/).filter(Boolean);
    const lines = [];
    let truncated = false;
    for (let pi = 0; pi < paragraphs.length; pi++) {
      const hardLines = paragraphs[pi].split("\n");
      for (let hi = 0; hi < hardLines.length; hi++) {
        let line = "";
        for (const ch of Array.from(hardLines[hi])) {
          const next = line + ch;
          if (line && ctx.measureText(next).width > maxWidth) {
            lines.push(line);
            line = ch;
            if (lines.length >= maxLines) {
              truncated = true;
              break;
            }
          } else {
            line = next;
          }
        }
        if (truncated) break;
        if (line) lines.push(line);
        if (lines.length >= maxLines) {
          truncated = hi < hardLines.length - 1 || pi < paragraphs.length - 1;
          break;
        }
      }
      if (truncated) break;
      if (pi < paragraphs.length - 1) lines.push("");
    }
    if (truncated && lines.length) {
      lines[lines.length - 1] = lines[lines.length - 1].replace(/[，。！？、；：,.!?;:\s]*$/, "") + "…";
    }
    return { lines, truncated };
  }

  function _canvasLinesHeight(lines, lineHeight) {
    return lines.reduce((sum, line) => sum + (line ? lineHeight : Math.round(lineHeight * 0.55)), 0);
  }

  function _drawCanvasLines(ctx, lines, x, y, lineHeight) {
    let nextY = y;
    lines.forEach(line => {
      if (line) ctx.fillText(line, x, nextY);
      nextY += line ? lineHeight : Math.round(lineHeight * 0.55);
    });
    return nextY;
  }

  function _loadShareImage(relPath, cacheKey) {
    if (cacheKey === "mark" && shareMarkPromise) return shareMarkPromise;
    if (cacheKey === "qr" && shareQrPromise) return shareQrPromise;
    const promise = (async () => {
      try {
        const resp = await fetch(chrome.runtime.getURL(relPath));
        if (!resp.ok) return null;
        const blobUrl = URL.createObjectURL(await resp.blob());
        const img = new Image();
        return await new Promise((resolve) => {
          img.onload = () => resolve(img);
          img.onerror = () => resolve(null);
          img.src = blobUrl;
        });
      } catch {
        return null;
      }
    })();
    if (cacheKey === "mark") shareMarkPromise = promise;
    if (cacheKey === "qr") shareQrPromise = promise;
    return promise;
  }

  function _loadShareMark() { return _loadShareImage("assets/share/margin-mark.png", "mark"); }
  function _loadShareQr() { return _loadShareImage("assets/share/qr-install.png", "qr"); }

  function _drawMarginLogoFallback(ctx, x, y, size) {
    _roundRect(ctx, x, y, size, size, 12);
    ctx.fillStyle = "#2F6D47";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.68)";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(x + size * 0.26, y + size * 0.36);
    ctx.lineTo(x + size * 0.74, y + size * 0.36);
    ctx.moveTo(x + size * 0.26, y + size * 0.56);
    ctx.lineTo(x + size * 0.58, y + size * 0.56);
    ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath();
    ctx.arc(x + size * 0.73, y + size * 0.62, size * 0.055, 0, Math.PI * 2);
    ctx.fill();
  }

  function _sharePayload(commentId, replyId) {
    const comments = load();
    const comment = comments.find(x => String(x.id) === String(commentId));
    const reply = comment?.replies?.find(x => String(x.id) === String(replyId));
    if (!comment || !reply || !reply.isAI) return null;
    const userMessages = [_normalizeShareText(comment.text)];
    for (const item of comment.replies || []) {
      if (String(item.id) === String(reply.id)) break;
      if (!item.isAI && item.text) userMessages.push(_normalizeShareText(item.text));
    }
    return {
      comment,
      reply,
      excerpt: _normalizeShareText(comment.excerpt || ""),
      userText: userMessages.filter(Boolean).join("\n\n"),
      aiText: _normalizeShareText(reply.text || ""),
      title: document.title || "",
      url: location.href,
    };
  }

  function _shareFallbackText(payload, variant = "full") {
    const key = _shareVariantKey(variant);
    if (key === "min_quote" || key === "min_comment") {
      const sourceLabel = key === "min_quote" ? "网页划线" : "我说";
      const sourceText = key === "min_quote" ? _shareDisplayText(payload.excerpt) : _shareDisplayText(payload.userText);
      return [
        "Margin",
        sourceText ? `${sourceLabel}：\n${sourceText}` : "",
        payload.aiText ? `Margin 的评注：\n${_shareDisplayText(payload.aiText)}` : "",
        SHARE_CLOSER,
        SHARE_INSTALL_TEXT,
      ].filter(Boolean).join("\n\n");
    }
    return [
      "Margin",
      payload.excerpt ? `网页划线：\n${_shareDisplayText(payload.excerpt)}` : "",
      payload.userText ? `我说：\n${_shareDisplayText(payload.userText)}` : "",
      payload.aiText ? `Margin 的评注：\n${_shareDisplayText(payload.aiText)}` : "",
      SHARE_CLOSER,
      SHARE_INSTALL_TEXT,
    ].filter(Boolean).join("\n\n");
  }

  const SHARE_FONTS = {
    serif: `"Source Han Serif SC", "Noto Serif SC", "Songti SC", "PingFang SC", serif`,
    sans: `"Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`,
    mono: `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace`,
  };
  const SHARE_LAYOUT = {
    width: 520,
    radius: 6,
    padX: 36,
    headerTop: 28,
    headerIcon: 32,
    headerGap: 10,
    headerBottom: 14,
    dividerH: 1,
    bodyTop: 22,
    bodyBottom: 8,
    labelH: 13,
    quoteLineH: 28.8,
    askLineH: 24.8,
    aiLineH: 24.75,
    aiCardPadX: 20,
    aiCardTopPad: 18,
    aiCardLabelTop: 12,
    aiCardTextTop: 10,
    aiCardBottomPad: 22,
    aiCardBottom: 26,
    footerTop: 22,
    footerBottom: 32,
    qrOuter: 74,
    qrImage: 66,
    qrGap: 10,
  };
  const SHARE_HEADER_H = SHARE_LAYOUT.headerTop + SHARE_LAYOUT.headerIcon + SHARE_LAYOUT.headerBottom + SHARE_LAYOUT.dividerH;
  const SHARE_FOOTER_H = SHARE_LAYOUT.footerTop + SHARE_LAYOUT.qrOuter + SHARE_LAYOUT.footerBottom;

  function _shareDateStamp() {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy} · ${mm} · ${dd}`;
  }

  function _setCanvasLetterSpacing(ctx, value) {
    try { ctx.letterSpacing = value; } catch {}
  }

  function _drawRoundedImage(ctx, img, x, y, width, height, radius) {
    ctx.save();
    _roundRect(ctx, x, y, width, height, radius);
    ctx.clip();
    ctx.drawImage(img, x, y, width, height);
    ctx.restore();
  }

  async function _drawShareHeader(ctx, x, y, width) {
    const { sans, mono } = SHARE_FONTS;
    const pad = SHARE_LAYOUT.padX;
    const iconSize = SHARE_LAYOUT.headerIcon;
    const iconX = x + pad;
    const iconY = y + SHARE_LAYOUT.headerTop;
    const centerY = iconY + iconSize / 2;
    const mark = await _loadShareMark();
    if (mark) {
      _drawRoundedImage(ctx, mark, iconX, iconY, iconSize, iconSize, 7);
    } else {
      _drawMarginLogoFallback(ctx, iconX, iconY, iconSize);
    }
    ctx.font = `700 17px ${sans}`;
    ctx.fillStyle = SHARE_INK;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    _setCanvasLetterSpacing(ctx, "0.1px");
    ctx.fillText("Margin", iconX + iconSize + SHARE_LAYOUT.headerGap, centerY);

    ctx.font = `400 11.5px ${mono}`;
    ctx.fillStyle = SHARE_MUTED_2;
    ctx.textAlign = "right";
    _setCanvasLetterSpacing(ctx, "0.4px");
    ctx.fillText(_shareDateStamp(), x + width - pad, centerY);
    ctx.textAlign = "left";
    _setCanvasLetterSpacing(ctx, "0px");

    ctx.fillStyle = SHARE_HAIR;
    ctx.fillRect(x + pad, y + SHARE_LAYOUT.headerTop + iconSize + SHARE_LAYOUT.headerBottom, width - pad * 2, SHARE_LAYOUT.dividerH);
    ctx.textBaseline = "alphabetic";
  }

  async function _drawShareFooter(ctx, x, y, width) {
    const { sans, serif } = SHARE_FONTS;
    const pad = SHARE_LAYOUT.padX;
    const qrOuter = SHARE_LAYOUT.qrOuter;
    const qrX = x + width - pad - qrOuter;
    const qrY = y + SHARE_LAYOUT.footerTop;
    const centerY = qrY + qrOuter / 2;

    ctx.fillStyle = SHARE_HAIR;
    ctx.fillRect(x + pad, y, width - pad * 2, 1);

    const qr = await _loadShareQr();
    _roundRect(ctx, qrX, qrY, qrOuter, qrOuter, 6);
    ctx.fillStyle = "#FFFFFF";
    ctx.fill();
    ctx.strokeStyle = SHARE_HAIR;
    ctx.lineWidth = 1;
    ctx.stroke();
    if (qr) {
      ctx.drawImage(qr, qrX + 4, qrY + 4, SHARE_LAYOUT.qrImage, SHARE_LAYOUT.qrImage);
    } else {
      _drawQrFallback(ctx, qrX + 4, qrY + 4, SHARE_LAYOUT.qrImage);
    }

    ctx.font = `400 16px ${serif}`;
    ctx.fillStyle = SHARE_MUTED;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(SHARE_CLOSER, x + pad, centerY);

    ctx.font = `400 10.5px ${sans}`;
    ctx.fillStyle = SHARE_MUTED;
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    const hintX = qrX - SHARE_LAYOUT.qrGap;
    const hintY = centerY - 14.7;
    ctx.fillText("扫码安装", hintX, hintY);
    ctx.fillText("Chrome 扩展", hintX, hintY + 14.7);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
  }

  function _drawQrFallback(ctx, x, y, size) {
    const cells = 29;
    const cell = size / cells;
    ctx.fillStyle = "#FFFFFF";
    ctx.fillRect(x, y, size, size);
    ctx.fillStyle = "#111111";
    function finder(cx, cy) {
      ctx.fillRect(x + cx * cell, y + cy * cell, cell * 7, cell * 7);
      ctx.fillStyle = "#FFFFFF";
      ctx.fillRect(x + (cx + 1) * cell, y + (cy + 1) * cell, cell * 5, cell * 5);
      ctx.fillStyle = "#111111";
      ctx.fillRect(x + (cx + 2) * cell, y + (cy + 2) * cell, cell * 3, cell * 3);
    }
    finder(1, 1);
    finder(21, 1);
    finder(1, 21);
    for (let row = 1; row < cells - 1; row++) {
      for (let col = 1; col < cells - 1; col++) {
        const inFinder = (row < 9 && col < 9) || (row < 9 && col > 19) || (row > 19 && col < 9);
        if (inFinder) continue;
        if (((row * 7 + col * 11 + row * col) % 5) < 2) {
          ctx.fillRect(x + col * cell, y + row * cell, Math.ceil(cell), Math.ceil(cell));
        }
      }
    }
  }

  function _measureFullCardBody(payload) {
    const measure = document.createElement("canvas").getContext("2d");
    const contentW = SHARE_LAYOUT.width - SHARE_LAYOUT.padX * 2;
    measure.font = `400 18px ${SHARE_FONTS.serif}`;
    const quote = _wrapCanvasText(measure, _shareDisplayText(payload.excerpt), contentW);
    measure.font = `400 15.5px ${SHARE_FONTS.serif}`;
    const userLines = _wrapCanvasText(measure, _shareDisplayText(payload.userText) || "想请 Margin 帮我看这段。", contentW);
    measure.font = `400 15px ${SHARE_FONTS.serif}`;
    const aiLines = _wrapCanvasText(measure, _shareDisplayText(payload.aiText), contentW - SHARE_LAYOUT.aiCardPadX * 2);
    const quoteH = _canvasLinesHeight(quote.lines, SHARE_LAYOUT.quoteLineH);
    const userH = _canvasLinesHeight(userLines.lines, SHARE_LAYOUT.askLineH);
    const aiTextH = _canvasLinesHeight(aiLines.lines, SHARE_LAYOUT.aiLineH);
    const aiCardH = SHARE_LAYOUT.aiCardTopPad + SHARE_LAYOUT.aiCardLabelTop + SHARE_LAYOUT.labelH
      + SHARE_LAYOUT.aiCardTextTop + aiTextH + SHARE_LAYOUT.aiCardBottomPad;
    const bodyH = SHARE_LAYOUT.bodyTop
      + SHARE_LAYOUT.labelH + 8 + quoteH + 24
      + SHARE_LAYOUT.labelH + 6 + userH + 24
      + aiCardH + SHARE_LAYOUT.aiCardBottom
      + SHARE_LAYOUT.bodyBottom;
    return { quote, userLines, aiLines, quoteH, userH, aiTextH, aiCardH, bodyH, truncated: false };
  }

  function _drawFullCardBody(ctx, x, yStart, m) {
    const { sans, serif } = SHARE_FONTS;
    const pad = SHARE_LAYOUT.padX;
    const contentW = SHARE_LAYOUT.width - pad * 2;
    let y = yStart + SHARE_LAYOUT.bodyTop;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";

    ctx.font = `600 11px ${sans}`;
    ctx.fillStyle = SHARE_BRAND;
    _setCanvasLetterSpacing(ctx, "1.3px");
    ctx.fillText("网页划线", x + pad, y);
    _setCanvasLetterSpacing(ctx, "0px");
    y += SHARE_LAYOUT.labelH + 8;
    ctx.font = `400 18px ${serif}`;
    ctx.fillStyle = SHARE_INK;
    y = _drawCanvasLines(ctx, m.quote.lines, x + pad, y, SHARE_LAYOUT.quoteLineH);

    y += 24;
    ctx.font = `600 11px ${sans}`;
    ctx.fillStyle = SHARE_BRAND;
    _setCanvasLetterSpacing(ctx, "1.3px");
    ctx.fillText("我说", x + pad, y);
    _setCanvasLetterSpacing(ctx, "0px");
    y += SHARE_LAYOUT.labelH + 6;
    ctx.font = `400 15.5px ${serif}`;
    ctx.fillStyle = SHARE_INK_2;
    y = _drawCanvasLines(ctx, m.userLines.lines, x + pad, y, SHARE_LAYOUT.askLineH);

    y += 24;
    _roundRect(ctx, x + pad, y, contentW, m.aiCardH, 12);
    ctx.fillStyle = "#FFFFFF";
    ctx.fill();
    ctx.strokeStyle = SHARE_HAIR;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = SHARE_BRAND;
    _roundRect(ctx, x + pad + SHARE_LAYOUT.aiCardPadX, y, 42, 3, 2);
    ctx.fill();
    ctx.font = `600 11px ${sans}`;
    ctx.fillStyle = SHARE_BRAND;
    _setCanvasLetterSpacing(ctx, "1.3px");
    ctx.fillText("Margin 的评注", x + pad + SHARE_LAYOUT.aiCardPadX, y + SHARE_LAYOUT.aiCardTopPad + SHARE_LAYOUT.aiCardLabelTop);
    _setCanvasLetterSpacing(ctx, "0px");
    ctx.font = `400 15px ${serif}`;
    ctx.fillStyle = SHARE_INK;
    _drawCanvasLines(
      ctx,
      m.aiLines.lines,
      x + pad + SHARE_LAYOUT.aiCardPadX,
      y + SHARE_LAYOUT.aiCardTopPad + SHARE_LAYOUT.aiCardLabelTop + SHARE_LAYOUT.labelH + SHARE_LAYOUT.aiCardTextTop,
      SHARE_LAYOUT.aiLineH
    );
    ctx.textBaseline = "alphabetic";
  }

  function _measureMinCardBody(payload, variant) {
    const measure = document.createElement("canvas").getContext("2d");
    const contentW = SHARE_LAYOUT.width - 80;
    const sourceText = _shareMinSource(payload, variant);
    const sourceLabel = _shareVariantKey(variant) === "min_quote" ? "网页划线 · MARGIN 的评注" : "我说 · MARGIN 的评注";
    measure.font = `400 17px ${SHARE_FONTS.serif}`;
    const quote = _wrapCanvasText(measure, sourceText, contentW);
    measure.font = `500 22px ${SHARE_FONTS.serif}`;
    const aiLines = _wrapCanvasText(measure, _shareDisplayText(payload.aiText), contentW);
    const quoteH = _canvasLinesHeight(quote.lines, 27.2);
    const aiTextH = _canvasLinesHeight(aiLines.lines, 34.1);
    const bodyH = 32 + SHARE_LAYOUT.labelH + 12 + quoteH + 26 + aiTextH + 10 + 24;
    return { sourceLabel, quote, aiLines, quoteH, aiTextH, bodyH, truncated: false };
  }

  function _drawMinCardBody(ctx, x, yStart, m) {
    const { sans, serif } = SHARE_FONTS;
    const totalH = m.bodyH;

    ctx.strokeStyle = SHARE_BRAND;
    ctx.globalAlpha = 0.42;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x + 18, yStart + 32);
    ctx.lineTo(x + 18, yStart + totalH - 24);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;

    let y = yStart + 32;
    ctx.textBaseline = "top";
    ctx.font = `600 11px ${sans}`;
    ctx.fillStyle = SHARE_BRAND;
    _setCanvasLetterSpacing(ctx, "1.4px");
    ctx.fillText(m.sourceLabel, x + 40, y);
    _setCanvasLetterSpacing(ctx, "0px");
    y += SHARE_LAYOUT.labelH + 12;
    ctx.font = `400 17px ${serif}`;
    ctx.fillStyle = SHARE_MUTED;
    y = _drawCanvasLines(ctx, m.quote.lines, x + 40, y, 27.2);

    y += 26;
    ctx.font = `500 22px ${serif}`;
    ctx.fillStyle = SHARE_INK;
    _drawCanvasLines(ctx, m.aiLines.lines, x + 40, y, 34.1);
    ctx.textBaseline = "alphabetic";
  }

  async function _createShareCardCanvas(payload, variant = "full") {
    const key = _shareVariantKey(variant);
    const { width } = SHARE_LAYOUT;
    const measured = key === "full"
      ? _measureFullCardBody(payload)
      : _measureMinCardBody(payload, key);
    const height = Math.ceil(SHARE_HEADER_H + measured.bodyH + SHARE_FOOTER_H);

    const dpr = 2;
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * dpr);
    canvas.height = Math.ceil(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    _roundRect(ctx, 0, 0, width, height, SHARE_LAYOUT.radius);
    ctx.clip();
    ctx.fillStyle = SHARE_PAPER;
    ctx.fillRect(0, 0, width, height);
    ctx.textBaseline = "alphabetic";

    await _drawShareHeader(ctx, 0, 0, width);
    if (key === "full") {
      _drawFullCardBody(ctx, 0, SHARE_HEADER_H, measured);
    } else {
      _drawMinCardBody(ctx, 0, SHARE_HEADER_H, measured);
    }
    await _drawShareFooter(ctx, 0, SHARE_HEADER_H + measured.bodyH, width);
    ctx.restore();

    canvas.dataset.truncated = "0";
    canvas.dataset.variant = key;
    return canvas;
  }

  async function _copyTextToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) throw new Error("copy command failed");
  }

  async function _copyShareImageOrText(state) {
    const blob = await new Promise(resolve => state.canvas.toBlob(resolve, "image/png"));
    if (blob && navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
      try {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        return "image";
      } catch (err) {
        console.warn("[KB] image clipboard failed, falling back to text:", err);
      }
    }
    await _copyTextToClipboard(state.text);
    return "text";
  }

  function _closeShareDialog() {
    if (shareDialogState?.closeOnEsc) {
      document.removeEventListener("keydown", shareDialogState.closeOnEsc, true);
    }
    if (shareDialogEl) shareDialogEl.remove();
    shareDialogEl = null;
    shareDialogState = null;
  }

  async function openShareDialog(commentId, replyId) {
    const payload = _sharePayload(commentId, replyId);
    if (!payload?.aiText) {
      showToast("这条还没有可分享的 AI 评注", "error");
      return;
    }
    _closeShareDialog();
    const shareId = kbRandomId("share");
    shareDialogEl = document.createElement("div");
    shareDialogEl.id = "kb-share-dialog";
    shareDialogEl.dataset.shareId = shareId;
    shareDialogEl.innerHTML = `
      <div class="kb-share-shell" role="dialog" aria-modal="true" aria-label="Margin 分享图">
        <div class="kb-share-header">
          <h4 class="kb-share-title">分享 Margin 评注</h4>
          <button type="button" class="kb-share-ghost" data-share-close="1">关闭</button>
        </div>
        <div class="kb-share-tabs" role="tablist">
          <button type="button" class="kb-share-tab is-active" role="tab" data-share-tab="full" aria-selected="true">完整版</button>
          <button type="button" class="kb-share-tab" role="tab" data-share-tab="min_quote" aria-selected="false">划线 + AI</button>
          <button type="button" class="kb-share-tab" role="tab" data-share-tab="min_comment" aria-selected="false">评论 + AI</button>
        </div>
        <div class="kb-share-preview" data-share-preview>
          <div class="kb-share-loading">正在生成分享图...</div>
        </div>
        <div class="kb-share-actions">
          <span class="kb-share-status" data-share-status></span>
          <button type="button" class="kb-share-ghost" data-share-copy-text="1">复制文字</button>
          <button type="button" class="kb-share-copy" data-share-copy-image="1">复制图片</button>
        </div>
      </div>
    `;
    document.body.appendChild(shareDialogEl);
    const closeOnEsc = (e) => {
      if (e.key === "Escape") _closeShareDialog();
    };
    document.addEventListener("keydown", closeOnEsc, true);
    shareDialogState = {
      id: shareId,
      payload,
      variant: "full",
      canvases: { full: null, min_quote: null, min_comment: null },
      get canvas() { return this.canvases[this.variant] || null; },
      get text() { return _shareFallbackText(this.payload, this.variant); },
      rendering: { full: false, min_quote: false, min_comment: false },
      closeOnEsc,
    };

    async function _renderVariant(variant) {
      if (!shareDialogEl || shareDialogEl.dataset.shareId !== shareId) return;
      const preview = shareDialogEl.querySelector("[data-share-preview]");
      const status = shareDialogEl.querySelector("[data-share-status]");
      if (status) status.textContent = "";
      if (shareDialogState.canvases[variant]) {
        const canvas = shareDialogState.canvases[variant];
        const img = document.createElement("img");
        img.alt = "Margin 分享图";
        img.src = canvas.toDataURL("image/png");
        preview.innerHTML = "";
        preview.appendChild(img);
        if (canvas.dataset.truncated === "1" && status) {
          status.textContent = "图片已为可读性截取，文字版保留完整内容";
        }
        return;
      }
      if (shareDialogState.rendering[variant]) return;
      shareDialogState.rendering[variant] = true;
      preview.innerHTML = `<div class="kb-share-loading">正在生成分享图...</div>`;
      try {
        const canvas = await _createShareCardCanvas(payload, variant);
        if (!shareDialogEl || shareDialogEl.dataset.shareId !== shareId) return;
        shareDialogState.canvases[variant] = canvas;
        if (shareDialogState.variant !== variant) return;
        const img = document.createElement("img");
        img.alt = "Margin 分享图";
        img.src = canvas.toDataURL("image/png");
        preview.innerHTML = "";
        preview.appendChild(img);
        if (canvas.dataset.truncated === "1" && status) {
          status.textContent = "图片已为可读性截取，文字版保留完整内容";
        }
      } catch (err) {
        console.error("[KB] share card render failed:", err);
        if (preview) preview.innerHTML = `<div class="kb-share-loading">生成分享图失败，可以先复制文字版。</div>`;
      } finally {
        shareDialogState.rendering[variant] = false;
      }
    }

    shareDialogEl.addEventListener("click", async (e) => {
      if (e.target === shareDialogEl || e.target.closest("[data-share-close]")) {
        _closeShareDialog();
        return;
      }
      const tabBtn = e.target.closest("[data-share-tab]");
      if (tabBtn) {
        const variant = tabBtn.dataset.shareTab;
        if (variant && variant !== shareDialogState.variant) {
          shareDialogState.variant = variant;
          shareDialogEl.querySelectorAll("[data-share-tab]").forEach(btn => {
            const isActive = btn.dataset.shareTab === variant;
            btn.classList.toggle("is-active", isActive);
            btn.setAttribute("aria-selected", isActive ? "true" : "false");
          });
          _renderVariant(variant);
        }
        return;
      }
      const status = shareDialogEl?.querySelector("[data-share-status]");
      if (e.target.closest("[data-share-copy-text]")) {
        try {
          await _copyTextToClipboard(shareDialogState.text);
          if (status) status.textContent = "已复制文字版";
          showToast("已复制文字版分享内容", "success");
        } catch {
          if (status) status.textContent = "复制失败";
          showToast("复制失败，请手动选中文字", "error");
        }
        return;
      }
      if (e.target.closest("[data-share-copy-image]")) {
        if (!shareDialogState?.canvas) {
          if (status) status.textContent = "分享图还在生成";
          return;
        }
        try {
          const result = await _copyShareImageOrText(shareDialogState);
          if (result === "image") {
            if (status) status.textContent = "已复制图片";
            showToast("已复制分享图，可以粘贴到微信或聊天窗口", "success");
          } else {
            if (status) status.textContent = "已复制文字版";
            showToast("浏览器暂不支持复制图片，已复制文字版", "success");
          }
          telemetryEvent("share_card_copied", "sidebar", {
            copy_result: result,
            share_variant: shareDialogState.variant,
            ai_reply_chars_bucket: charBucket(payload.aiText),
          }, payload.comment);
        } catch {
          if (status) status.textContent = "复制失败";
          showToast("复制失败，请检查浏览器剪贴板权限", "error");
        }
      }
    });
    telemetryEvent("share_card_opened", "sidebar", {
      ai_reply_chars_bucket: charBucket(payload.aiText),
    }, payload.comment);
    _renderVariant("full");
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
      let fulfilledPending = false;
      for (const rr of remote.replies || []) {
        const text = rr.content || "";
        if (!text.trim()) continue;
        const isAI = rr.author === "agent";
        if (_remoteReplyAlreadyLocal(c.replies, rr, isAI, text)) continue;
        const replyRecord = {
          id: Date.now() + c.replies.length,
          remoteReplyId: rr.id,
          text,
          isAI,
          debugMeta: rr.debug_meta || null,
          createdAt: rr.created_at || new Date().toISOString(),
        };
        const replaceReplyId = isAI ? c.aiPending?.replaceReplyId : null;
        const replaceIdx = replaceReplyId
          ? c.replies.findIndex(r => String(r.id) === String(replaceReplyId))
          : -1;
        if (replaceIdx >= 0) {
          c.replies[replaceIdx] = replyRecord;
          if (isAI) fulfilledPending = true;
        } else {
          c.replies.push(replyRecord);
        }
        changed = true;
        if (isAI) addedAI = true;
      }
      if (c.aiPending && (fulfilledPending || countAIReplies(c) > (c.aiPending.existingAIReplyCount || 0))) {
        delete c.aiPending;
        changed = true;
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

  let _pendingAiResumeTimer = null;
  function resumePendingAiReplies() {
    const pending = load().filter(c => c.aiPending && c.agentCommentId && !_askAIRunning.has(c.id));
    pending.forEach(c => syncCommentFromBackend(c.id, { notify: true }));
    if (!pending.length && _pendingAiResumeTimer) {
      clearInterval(_pendingAiResumeTimer);
      _pendingAiResumeTimer = null;
    }
  }
  function startPendingAiResumeLoop() {
    if (_pendingAiResumeTimer) return;
    if (!load().some(c => c.aiPending && c.agentCommentId)) return;
    resumePendingAiReplies();
    _pendingAiResumeTimer = setInterval(resumePendingAiReplies, 5000);
  }

  function jumpToAiReply(commentId) {
    if (!panelEl) buildPanel();
    if (!panelOpen) {
      _showPanel();
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
      _showPanel();
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
    const parseDebugMeta = (r) => {
      if (!r || !r.debugMeta) return {};
      if (typeof r.debugMeta !== "string") return r.debugMeta || {};
      try { return JSON.parse(r.debugMeta) || {}; } catch { return {}; }
    };
    const isFailedAIReply = (r) => {
      if (!r?.isAI) return false;
      const dm = parseDebugMeta(r);
      if (dm.status === "error" || dm.status === "failed" || dm.error) return true;
      return /^(AI 回复失败|Agent 执行出错)/.test(r.text || "");
    };
    const isPendingAIReply = (r) => r?.isAI && /^AI 仍在处理中/.test(r.text || "");
    const feedbackEligible = (r) => r?.isAI && !isFailedAIReply(r) && !isPendingAIReply(r);
    const renderReportPanel = (r) => {
      if (!r.feedbackDraftOpen) return "";
      const rawOpts = r.problemReportOptions || {};
      const opts = {
        include_conversation: rawOpts.include_conversation ?? true,
        include_selection: rawOpts.include_selection ?? true,
        include_page_info: rawOpts.include_page_info ?? true,
        include_model_io: rawOpts.include_model_io ?? true,
      };
      const preview = r.problemReportPreview || {};
      const counts = preview.counts || {};
      const reportOnly = Boolean(r.problemReportOnly);
      const positiveExample = Boolean(r.positiveExampleDraftOpen || r.problemReportRating === "positive_example");
      const buttonLabel = positiveExample ? "发送好例子" : (reportOnly ? "发送问题报告" : "发送反馈 + 诊断包");
      const note = positiveExample
        ? "会把这次答得好的现场发给开发者，帮助我们保留有效的回答方式。你可以取消不想发送的部分。"
        : (reportOnly
            ? "我会把这次问题需要的材料发给开发者排查。你可以取消不想发送的部分。"
            : "点踩会一起发送诊断材料，帮助开发者还原这次 AI 为什么没答好。你可以取消不想发送的部分。");
      const selectedLabels = [
        [opts.include_conversation, "这条评论和 AI 回答"],
        [opts.include_selection, "你划线的内容"],
        [opts.include_page_info, "网页标题和链接"],
        [opts.include_model_io, "模型输入和输出"],
      ].filter(([enabled]) => enabled).map(([, label]) => label);
      const conversationDesc = positiveExample
        ? "用于看清你问了什么、AI 这次哪里答得好。"
        : "用于看清你问了什么、AI 哪里没接住。";
      const selectionDesc = positiveExample
        ? "用于还原 AI 这次正确理解的上下文。"
        : "用于还原 AI 当时应该理解的上下文。";
      const modelIoDesc = positiveExample
        ? "最敏感；用于保留这次有效的 prompt、检索和回答方式。"
        : "最敏感；用于排查 prompt、检索和超时问题。";
      const selectedHtml = selectedLabels.length
        ? `<ul>${selectedLabels.map(label => `<li>${escapeHtml(label)}</li>`).join("")}</ul>`
        : `<div>只发送排查所需的基础信息，不发送正文。</div>`;
      const callTraceText = positiveExample
        ? ((counts.ledger_calls || counts.request_snapshots)
            ? "已找到这次 AI 调用记录，可以把有效的上下文、模型和回答方式留作好例子。"
            : "还没找到这次 AI 调用记录；仍会保存你勾选的页面和对话线索。")
        : ((counts.ledger_calls || counts.request_snapshots)
            ? "已找到这次 AI 调用记录，可以排查是否超时、失败、走错模型或拿错上下文。"
            : "还没找到这次 AI 调用记录；仍会保存你勾选的页面和对话线索。");
      const previewHtml = r.problemReportPreviewLoading
        ? `<div class="kb-report-preview">正在检查这次问题能否还原...</div>`
        : r.problemReportPreviewError
          ? `<div class="kb-report-preview warn">暂时没法生成发送确认：${escapeHtml(String(r.problemReportPreviewError))}</div>`
          : `<div class="kb-report-preview">
              <div><b>会发送：</b></div>
              ${selectedHtml}
              <div style="margin-top:6px">${escapeHtml(callTraceText)}</div>
            </div>`;
      return `
        <div class="kb-feedback-panel">
          <textarea id="kb-feedback-ta-${c.id}-${r.id}" placeholder="${positiveExample ? "补一句这次为什么有用（可选）" : (reportOnly ? "补一句你看到的问题（可选）" : "哪里不对、没帮上忙，直接写一句")}">${escapeHtml(r.problemReportNote || "")}</textarea>
          <div class="kb-feedback-note">${note}</div>
          <div class="kb-report-options">
            <label class="kb-report-option">
              <input type="checkbox" id="kb-report-conversation-${c.id}-${r.id}" ${opts.include_conversation ? "checked" : ""}>
              <span class="kb-report-check" aria-hidden="true"></span>
              <span class="kb-report-copy"><span class="kb-report-title">这条评论和 AI 回答</span><span class="kb-report-desc">${conversationDesc}</span></span>
            </label>
            <label class="kb-report-option">
              <input type="checkbox" id="kb-report-selection-${c.id}-${r.id}" ${opts.include_selection ? "checked" : ""}>
              <span class="kb-report-check" aria-hidden="true"></span>
              <span class="kb-report-copy"><span class="kb-report-title">你划线的内容</span><span class="kb-report-desc">${selectionDesc}</span></span>
            </label>
            <label class="kb-report-option">
              <input type="checkbox" id="kb-report-page-${c.id}-${r.id}" ${opts.include_page_info ? "checked" : ""}>
              <span class="kb-report-check" aria-hidden="true"></span>
              <span class="kb-report-copy"><span class="kb-report-title">网页标题和链接</span><span class="kb-report-desc">用于定位发生问题的页面。</span></span>
            </label>
            <label class="kb-report-option">
              <input type="checkbox" id="kb-report-model-${c.id}-${r.id}" ${opts.include_model_io ? "checked" : ""}>
              <span class="kb-report-check" aria-hidden="true"></span>
              <span class="kb-report-copy"><span class="kb-report-title">模型输入和输出</span><span class="kb-report-desc">${modelIoDesc}</span></span>
            </label>
          </div>
          ${previewHtml}
          <div class="kb-feedback-actions">
            <button type="button" class="kb-ai-btn" data-ai-feedback-submit="${c.id}" data-reply-id="${r.id}" data-report-only="${reportOnly ? "1" : "0"}" data-positive-example="${positiveExample ? "1" : "0"}">${buttonLabel}</button>
            <button type="button" class="kb-reply-btn" data-ai-feedback-cancel="${c.id}" data-reply-id="${r.id}">取消</button>
          </div>
        </div>`;
    };
    const renderPositiveExamplePrompt = (r) => {
      if (!r.positiveExamplePromptOpen || r.positiveExampleSubmitted || r.feedbackDraftOpen) return "";
      return `<div class="kb-positive-example-prompt">
        <div>已记录赞。愿意把这次作为一个好例子发给开发者吗？</div>
        <div class="kb-positive-example-actions">
          <button type="button" class="kb-reply-btn" data-ai-positive-example-open="${c.id}" data-reply-id="${r.id}">发送好例子</button>
          <button type="button" class="kb-reply-btn" data-ai-positive-example-dismiss="${c.id}" data-reply-id="${r.id}">不用</button>
        </div>
      </div>`;
    };
    const renderFeedbackControls = (r) => {
      if (!r?.isAI) return "";
      if (!feedbackEligible(r)) {
        return `<div class="kb-ai-feedback">
          <button type="button" class="kb-feedback-chip" data-ai-report-open="${c.id}" data-reply-id="${r.id}">报告问题</button>
          ${r.problemReportSubmitted ? `<span class="kb-feedback-done">已记录</span>` : ""}
          ${renderReportPanel(r)}
        </div>`;
      }
      return `<div class="kb-ai-feedback">
        <button type="button" class="kb-feedback-chip ${r.feedbackRating === "up" ? "active" : ""}" data-ai-feedback-up="${c.id}" data-reply-id="${r.id}">赞</button>
        <button type="button" class="kb-feedback-chip ${r.feedbackRating === "down" ? "active" : ""}" data-ai-feedback-down="${c.id}" data-reply-id="${r.id}">踩</button>
        ${r.feedbackSubmitted ? `<span class="kb-feedback-done">已记录</span>` : ""}
        ${r.positiveExampleSubmitted ? `<span class="kb-feedback-done">好例子已发送</span>` : ""}
        ${r.problemReportSubmitted && !r.feedbackSubmitted ? `<span class="kb-feedback-done">问题已记录</span>` : ""}
        ${renderPositiveExamplePrompt(r)}
        ${renderReportPanel(r)}
      </div>`;
    };
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
            const memoryOk = (ctx.attention_memory ?? ctx.notion_memory) ? "✓" : "✗";
            const selOk = ctx.selected_text ? "✓" : "✗";
            debugHtml = `
              <details class="kb-debug">
                <summary>▶ Debug</summary>
                <div class="kb-debug-body">
                  [v1] @${dm.agent_type} · ${dm.elapsed_s}s · ~${dm.prompt_tokens_est} tokens<br>
                  Context: project✓ memory${memoryOk} selected_text${selOk}<br>
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
      const retryHtml = isFailedAIReply(r) && !_askAIRunning.has(c.id)
        ? `<div class="kb-reply-actions"><button class="kb-ai-btn" data-regenerate-ai="${c.id}" data-reply-id="${r.id}">重新生成</button></div>`
        : "";
      return `
      <div class="kb-reply ${r.isAI ? "ai" : "user"}">
        <div class="kb-reply-label">${labelHtml}</div>
        <div class="kb-reply-body">${bodyHtml}</div>
        ${debugHtml}
        ${renderFeedbackControls(r)}
        ${retryHtml}
      </div>`;
    }).join("");
    const aiReplies = c.replies.filter(r => r.isAI);
    const hasAnyAI = aiReplies.length > 0;
    const shareableAiReply = [...c.replies].reverse().find(r => r.isAI && feedbackEligible(r));
    const shareHtml = shareableAiReply
      ? `<button type="button" class="kb-share-btn" data-share-reply="${c.id}" data-reply-id="${shareableAiReply.id}">分享图</button>`
      : "";
    let actionHtml = "";
    if (_askAIRunning.has(c.id) || c.aiPending) {
      actionHtml = `${shareHtml}<span style="color:var(--kb-blue);font-size:11px;font-family:'JetBrains Mono',monospace;letter-spacing:0.04em;">AI 思考中…</span>`;
    } else if (_aiUnreadCommentIds.has(c.id)) {
      actionHtml = `${shareHtml}<button class="kb-ai-ready-btn" data-jump-ai="${c.id}">AI 已回复 · 查看</button>`;
    } else if (!hasAnyAI) {
      actionHtml = `<button class="kb-ai-btn" data-ask-ai="${c.id}">请 AI 回复</button>`;
    } else {
      actionHtml = `${shareHtml}<button class="kb-reply-btn" data-open-reply="${c.id}">继续追问</button>`;
    }
    return `
      <div class="kb-cmt-card ${_aiUnreadCommentIds.has(c.id) ? "kb-ai-unread" : ""}" id="kb-cmt-${c.id}">
        ${c.excerpt ? `<div class="kb-cmt-quote">"${escapeHtml(c.excerpt.slice(0,100))}${c.excerpt.length>100?"…":""}"</div>` : ""}
        <div class="kb-cmt-content" id="kb-cmt-content-${c.id}">
          <div class="kb-cmt-text">${escapeHtml(c.text)}</div>
          <div class="kb-cmt-meta">${timeStr}</div>
          ${repliesHtml}
        </div>
        <button class="kb-cmt-expand kb-expand-hidden" id="kb-cmt-expand-${c.id}" data-expand="${c.id}">展开全部 ↓</button>
        <div class="kb-cmt-actions">
          ${actionHtml}
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
    const c = load().find(x => String(x.id) === String(commentId));
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
    const feedbackUp = e.target.closest("[data-ai-feedback-up]");
    if (feedbackUp) {
      submitAiFeedback(feedbackUp.dataset.aiFeedbackUp, feedbackUp.dataset.replyId, "up");
      return;
    }
    const feedbackDown = e.target.closest("[data-ai-feedback-down]");
    if (feedbackDown) {
      openProblemReportPanel(feedbackDown.dataset.aiFeedbackDown, feedbackDown.dataset.replyId, { rating: "down" });
      return;
    }
    const reportOpen = e.target.closest("[data-ai-report-open]");
    if (reportOpen) {
      openProblemReportPanel(reportOpen.dataset.aiReportOpen, reportOpen.dataset.replyId, { rating: "problem", reportOnly: true });
      return;
    }
    const positiveExampleOpen = e.target.closest("[data-ai-positive-example-open]");
    if (positiveExampleOpen) {
      openProblemReportPanel(positiveExampleOpen.dataset.aiPositiveExampleOpen, positiveExampleOpen.dataset.replyId, {
        rating: "positive_example",
        positiveExample: true,
      });
      return;
    }
    const positiveExampleDismiss = e.target.closest("[data-ai-positive-example-dismiss]");
    if (positiveExampleDismiss) {
      patchReply(positiveExampleDismiss.dataset.aiPositiveExampleDismiss, positiveExampleDismiss.dataset.replyId, {
        positiveExamplePromptOpen: false,
        positiveExampleDismissed: true,
      });
      return;
    }
    const reportPreview = e.target.closest("[data-ai-report-preview]");
    if (reportPreview) {
      const commentId = reportPreview.dataset.aiReportPreview;
      const replyId = reportPreview.dataset.replyId;
      const comments = load();
      const c = comments.find(x => String(x.id) === String(commentId));
      const r = c?.replies?.find(x => String(x.id) === String(replyId));
      refreshProblemReportPreview(commentId, replyId, r?.problemReportRating || (r?.problemReportOnly ? "problem" : "down"));
      return;
    }
    const reportOption = e.target.closest(".kb-report-options input");
    if (reportOption) {
      const m = reportOption.id.match(/^kb-report-(conversation|selection|page|model)-(.+)-(.+)$/);
      if (m) {
        const [, , commentId, replyId] = m;
        const ta = document.getElementById(`kb-feedback-ta-${commentId}-${replyId}`);
        patchReply(commentId, replyId, {
          problemReportOptions: getProblemReportOptions(commentId, replyId),
          problemReportNote: ta?.value || "",
        });
      }
      return;
    }
    const feedbackSubmit = e.target.closest("[data-ai-feedback-submit]");
    if (feedbackSubmit) {
      const commentId = feedbackSubmit.dataset.aiFeedbackSubmit;
      const replyId = feedbackSubmit.dataset.replyId;
      const ta = document.getElementById(`kb-feedback-ta-${commentId}-${replyId}`);
      if (feedbackSubmit.dataset.positiveExample === "1") {
        submitPositiveExampleReport(commentId, replyId, ta?.value || "");
      } else if (feedbackSubmit.dataset.reportOnly === "1") {
        submitProblemReportOnly(commentId, replyId, ta?.value || "");
      } else {
        submitAiFeedback(commentId, replyId, "down", ta?.value || "");
      }
      return;
    }
    const feedbackCancel = e.target.closest("[data-ai-feedback-cancel]");
    if (feedbackCancel) {
      patchReply(feedbackCancel.dataset.aiFeedbackCancel, feedbackCancel.dataset.replyId, {
        feedbackDraftOpen: false,
        positiveExampleDraftOpen: false,
      });
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
    telemetryEvent("followup_created", "sidebar", {
      followup_chars_bucket: charBucket(text),
      thread_turn_count_before: Math.max(0, threadTurnCount(c) - 1),
    }, c);
    if (c.agentCommentId) {
      try {
        const identity = await telemetryIdentity(c, "sidebar");
        await fetch(`http://localhost:8766/comments/${c.agentCommentId}/reply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text, ...identity }),
        });
      } catch { /* 离线时静默 */ }
    }

    // 本地记忆库更新；外部备份由后端按配置处理
    syncCaptureToVault(c);

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
    const initialExcerpt = currentExcerpt || _savedSelection || "";
    telemetryEvent("sidebar_comment_created", "sidebar", {
      comment_chars_bucket: charBucket(text),
      active_note_chars_bucket: charBucket(text),
      selected_text_chars_bucket: charBucket(initialExcerpt),
      has_selected_text: Boolean(initialExcerpt),
      thread_turn_count_before: 0,
    }, c);
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
      const identity = await telemetryIdentity(c, "sidebar");
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
          ...identity,
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

    // 评论提交时立即确认本地保存；AI 回复完成后再同步同一条 capture
    syncCaptureToVault(c);

    setTimeout(() => { status.textContent = ""; }, 3000);
    btn.disabled = false;
    // 确保面板可见（用户可能在面板关闭时提交了评论）
    if (panelEl && !panelOpen) {
      _showPanel();
    }
    render();
    // 评论已发送 → 收回底部输入区，避免常驻挡视线
    collapseInputArea();
  }

  // ── Capture upsert：每条划线/评论对应一条本地记录；外部备份由后端处理 ──
  function syncCaptureToVault(comment) {
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
    fetch("http://localhost:8766/captures/upsert", {
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
        console.error("[KB] capture upsert HTTP error:", r.status, r.statusText);
        return r.text().then(t => { throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`); });
      }
      return r.json();
    }).then(resp => {
      if (resp.success) {
        if (resp.notionSynced === false && resp.notionError) console.warn("[KB] optional Notion backup not synced:", resp.notionError);
        if (resp.pageId && !comment.notionPageId) {
          comment.notionPageId = resp.pageId;
          const comments = load();
          const match = comments.find(x => x.id === comment.id);
          if (match) { match.notionPageId = resp.pageId; save(comments); }
        }
        showToast(captureSavedToast(resp, "已保存到本地记忆库"), "success");
      } else {
        const detail = resp.detail || resp.error || "未知错误";
        console.error("[KB] capture upsert failed:", detail);
        showToast("✗ 本地保存失败：" + detail, "error");
      }
    }).catch(err => {
      console.error("[KB] capture upsert error:", err.message || err);
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
  async function askAI(commentId, options = {}) {
    if (_askAIRunning.has(commentId)) return; // AI 还在回复，忽略
    _askAIRunning.add(commentId);
    updateCommentCard(commentId);
    // 立即禁用追问输入（不等 render，直接操作 DOM）
    _lockReplyInput(commentId, true);

    const comments = load();
    const c = comments.find(x => x.id === commentId);
    if (!c) { _askAIRunning.delete(commentId); _lockReplyInput(commentId, false); return; }
    const _askStartMs = Date.now();
    const _requestedVia = options.replaceReplyId ? "regenerate" : (options.selectedText ? "better_question" : "ask_ai");
    telemetryEvent("ai_reply_requested", "sidebar", {
      requested_via: _requestedVia,
      existing_ai_reply_count: countAIReplies(c),
      thread_turn_count_before: threadTurnCount(c),
      replace_reply: Boolean(options.replaceReplyId),
    }, c);

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

      // 多轮压平 bug 已修：不再把用户消息拼接 PATCH 进 comment。
      // 后端 rerun 时会从 replies 表重建 用户/AI 交错的真实对话（comments.comment 保持首轮原文）。
      // conversationComment 仅用于下方"后端 comment 不存在"的恢复路径（离线期间建的 thread 补建档案）。
      const allUserMessages = [c.text, ...c.replies.filter(r => !r.isAI).map(r => r.text)];
      const conversationComment = allUserMessages.join("\n\n---追问---\n\n");

      // 记录 rerun 前已有的 agent reply 数量，轮询时等待新增
      let existingAgentReplyCount = 0;
      const existingAIReplyCount = countAIReplies(c);

      // 如果已有 agentCommentId，rerun with latest conversation；否则新建
      if (agentCommentId) {
        setAiPending(commentId, {
          agentCommentId,
          replaceReplyId: options.replaceReplyId || null,
          existingAIReplyCount,
        });
        startPendingAiResumeLoop();
        // 先查当前有多少条 agent reply
        try {
          const preResp = await fetch(`http://localhost:8766/comments/${agentCommentId}`);
          if (preResp.ok) {
            const preData = await preResp.json();
            existingAgentReplyCount = preData.replies.filter(r => r.author === "agent").length;
            setAiPending(commentId, {
              agentCommentId,
              replaceReplyId: options.replaceReplyId || null,
              existingAIReplyCount,
              existingAgentReplyCount,
            });
          }
        } catch { /* ignore */ }
        // 直接 rerun：追问已通过 POST /reply 落库，后端自己重建对话历史
        const rerunResp = await fetch(`http://localhost:8766/comments/${agentCommentId}/rerun`, { method: "POST" });
        if (!rerunResp.ok) throw new Error(`无法重新召唤 AI（HTTP ${rerunResp.status}）`);
      } else {
        const surrounding = options.surroundingText || getSurroundingText(c.excerpt || "");
        const identity = await telemetryIdentity(c, "sidebar");
        const resp = await fetch("http://localhost:8766/comments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page_url: options.pageUrl || location.href,
            page_title: options.pageTitle || document.title,
            selected_text: options.selectedText || c.excerpt || "",
            surrounding_text: surrounding,
            page_content: options.pageContent || "",
            comment: conversationComment,
            no_agent: false,
            ...identity,
          }),
        });
        if (!resp.ok) throw new Error("agent_api 不可用");
        const data = await resp.json();
        agentCommentId = data.id;
        const fresh = load();
        const match = fresh.find(x => x.id === commentId);
        if (match) { match.agentCommentId = agentCommentId; save(fresh); }
        setAiPending(commentId, {
          agentCommentId,
          replaceReplyId: options.replaceReplyId || null,
          existingAIReplyCount,
          existingAgentReplyCount,
        });
        startPendingAiResumeLoop();
      }

      // 轮询等待 agent 回复（每5秒，最多360次=30分钟）
      let reply = null;
      let replyDebugMeta = null;
      let backendReplyId = null;
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
          backendReplyId = lastReply.id || null;
          break;
        }
      }

      if (reply) {
        const shouldNotify = !_isCommentCardVisible(commentId);
        if (options.replaceReplyId) {
          replaceReply(commentId, options.replaceReplyId, reply, true, replyDebugMeta, backendReplyId);
        } else {
          addReply(commentId, reply, true, replyDebugMeta, backendReplyId);
        }
        clearAiPending(commentId);
        if (shouldNotify) _markAiReplyReady(commentId);
        // 每次 AI 回复后同步 capture（本地为主，外部备份可选）
        const freshC = load().find(x => x.id === commentId);
        if (freshC) {
          // 字段瘦身：stage/role/model/token/cost 都查 ledger（用 thread_telemetry_id 关联）
          telemetryEvent("ai_reply_completed", "sidebar", {
            requested_via: _requestedVia,
            reply_chars_bucket: charBucket(reply),
            thread_turn_count_before: Math.max(0, threadTurnCount(freshC) - 1),
            elapsed_s_user: Math.round((Date.now() - _askStartMs) / 100) / 10,
            status: "success",
          }, freshC);
          syncCaptureToVault(freshC);
        }
      } else {
        // 超时合并进 ai_reply_failed，按 error_category="timeout" 区分
        const pendingText = "AI 仍在处理中，请稍候刷新页面查看结果。";
        if (options.replaceReplyId) {
          replaceReply(commentId, options.replaceReplyId, pendingText, true);
        } else {
          addReply(commentId, pendingText, true);
        }
        const freshC = load().find(x => x.id === commentId);
        telemetryEvent("ai_reply_failed", "sidebar", {
          requested_via: _requestedVia,
          error_category: "timeout",
          error_code: "poll_timeout",
          elapsed_s_user: Math.round((Date.now() - _askStartMs) / 100) / 10,
        }, freshC || c);
        clearAiPending(commentId);
      }
    } catch (err) {
      const msg = err.message || String(err);
      const freshC = load().find(x => x.id === commentId);
      telemetryEvent("ai_reply_failed", "sidebar", {
        requested_via: _requestedVia,
        ...categorizeClientError(msg),
        elapsed_s_user: Math.round((Date.now() - _askStartMs) / 100) / 10,
      }, freshC || c);
      let failureText = "";
      if (msg.includes("Extension context invalidated")) {
        failureText = "AI 回复失败：插件已失效，请刷新页面后重试。";
      } else if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
        failureText = isWindowsPlatform()
          ? "AI 回复失败：无法连接本地服务。请检查是否已运行 .\\start.ps1 启动后端"
          : "AI 回复失败：无法连接本地服务。请检查是否已运行 start.sh 启动后端（终端执行：bash start.sh）";
      } else {
        failureText = "AI 回复失败：" + msg;
      }
      if (options.replaceReplyId) {
        replaceReply(commentId, options.replaceReplyId, failureText, true);
      } else {
        addReply(commentId, failureText, true);
      }
      clearAiPending(commentId);
    }

    removeThinking();
    _askAIRunning.delete(commentId);
    updateCommentCard(commentId);
    _lockReplyInput(commentId, false);
  }

  function askAIForQuestionExcerpt(payload = {}) {
    const excerpt = (payload.excerpt || payload.question || "").trim();
    if (!excerpt) {
      showToast("没有可提问的内容", "error");
      return null;
    }
    const comment = (payload.comment || "请直接回答这句下一问。").trim();
    buildPanel();
    highlightByText(excerpt);
    currentExcerpt = excerpt;
    const c = addComment(excerpt, comment);
    telemetryEvent("sidebar_comment_created", "sidebar", {
      comment_chars_bucket: charBucket(comment),
      active_note_chars_bucket: charBucket(comment),
      selected_text_chars_bucket: charBucket(excerpt),
      has_selected_text: true,
      requested_via: "better_question",
      thread_turn_count_before: 0,
    }, c);
    _showPanel();
    render();
    setTimeout(() => _anchorCardForExcerpt(excerpt, { scroll: true, flash: true }), 120);
    askAI(c.id, {
      selectedText: excerpt,
      surroundingText: payload.contextText || getSurroundingText(excerpt),
      pageUrl: payload.pageUrl || location.href,
      pageTitle: payload.pageTitle || document.title,
      pageContent: payload.pageContent || "",
    });
    showToast("已在右侧评注里召唤 AI", "success");
    return c.id;
  }

  // ── 对外接口：打开评论面板（高亮由调用方处理）──
  // 划线后调用此函数：展开输入区，让用户写一条评论
  function open(excerpt, url, title) {
    try {
      currentExcerpt = excerpt;
      buildPanel();
      if (!panelEl) throw new Error("comment panel element was not created");
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
      _showPanel();
      render();
      syncVisibleCommentsFromBackend({ notify: false });
      startPendingAiResumeLoop();
      // 展开输入区 + 聚焦（这是划线触发的）
      expandInputArea(true);
    } catch (err) {
      console.error("[KB] open comment panel failed:", err);
      showToast("评注面板打开失败：请刷新当前网页后重试", "error");
    }
  }

  // 页面加载时恢复高亮 + 渲染已有评论
  let initialized = false;
  function init() {
    if (initialized) return;
    initialized = true;
    // 关键：stylesheet 必须先注入，否则 restoreHighlights 重建出来的 mark 会失去 .kb-comment-highlight 的样式（绿色高亮底）
    injectStyles();
    window.addEventListener("resize", _syncPanelWidthToViewport, { passive: true });
    restoreHighlights();
    restoreCommentHighlights();
    const comments = load();
    if (comments.length > 0) {
      buildPanel({ hidden: !panelOpen });
      if (!panelOpen) {
        panelEl.classList.add("kb-btn-hidden");
        document.body.style.marginRight = "";
      } else {
        _applyPanelWidth(panelWidth);
      }
      render();
      syncVisibleCommentsFromBackend({ notify: true });
      startPendingAiResumeLoop();
    }
    // badge 响应式更新：有高亮或评论时常驻显示
    updateBadge();
    hydratePageCommentsFromBackend();
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
    if (e.data && e.data.__kb_action === 'kb_better_question_ask_ai') {
      const localCommentId = askAIForQuestionExcerpt(e.data.payload || {});
      window.postMessage({
        __kb_action_result: 'kb_better_question_ask_ai',
        actionId: e.data.actionId,
        ok: Boolean(localCommentId),
        localCommentId,
      }, "*");
    }
  });

  return {
    open,
    render,
    load,
    doHighlight,
    doHighlightAndOpenComment,
    highlightByText,
    saveHighlightToVault,
    saveHighlightToNotion: saveHighlightToVault,
    askAIForQuestionExcerpt,
    debugMarkUnreadAiReply,
    version: KB_CONTENT_VERSION,
  };
})();

try {
  window.kbCommentSystem = commentSystem;
} catch {}
