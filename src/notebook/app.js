// Margin 笔记本 - 真实数据驱动，无 mock
// 后端：~/Documents/ai/coding/knowledge-base-extension/backend/agent_api.py
// 设计：~/mem-ai/docs/memory-backend-design.md

const API_BASE = "http://localhost:8766";
const BETTER_QUESTION_ASK_AI = "kb_better_question_ask_ai";

// ─── 工具 ───
function $(id) { return document.getElementById(id); }

// ─── 运营埋点（notebook 子页打开）───
// install_id 用 chrome.storage.local，跨扩展页共享；session_id 用 localStorage（本页生命周期内）
const NB_TELEMETRY_INSTALL_KEY = "kb_telemetry_install_id_v1";
const NB_TELEMETRY_SESSION_KEY = "kb_telemetry_session_v1";
const NB_TELEMETRY_SESSION_TTL_MS = 30 * 60 * 1000;
function nbRandomId(prefix) {
  const raw = (globalThis.crypto && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}_${raw}`;
}
// install_id 必须跨刷新稳定。优先级：chrome.storage.local (跨扩展页共享) →
// localStorage (per-origin 持久) → 兜底新 UUID 并写回 localStorage。
// 重要：chrome.storage 不可用时一定要回退到 localStorage，不能直接生成随机 id —
// 否则每次刷新都产生新"用户"，把 admin 的 DAU 污染成假数据。
async function getNotebookInstallId() {
  return new Promise((resolve) => {
    let storageAvailable = true;
    try {
      chrome.storage.local.get([NB_TELEMETRY_INSTALL_KEY], (r) => {
        let id = r && r[NB_TELEMETRY_INSTALL_KEY];
        if (!id) {
          // chrome.storage 里没有，先看 localStorage 有没有迁移过来的
          let legacy = "";
          try { legacy = localStorage.getItem(NB_TELEMETRY_INSTALL_KEY) || ""; } catch {}
          id = legacy || nbRandomId("install");
          try { chrome.storage.local.set({ [NB_TELEMETRY_INSTALL_KEY]: id }); } catch {}
        }
        try { localStorage.setItem(NB_TELEMETRY_INSTALL_KEY, id); } catch {}
        resolve(id);
      });
    } catch {
      storageAvailable = false;
    }
    if (!storageAvailable) {
      // chrome.storage 整个不可用（极端 case，比如 notebook 被错误地以非扩展上下文打开）
      // 回退到 localStorage，保持跨刷新稳定
      let id = "";
      try { id = localStorage.getItem(NB_TELEMETRY_INSTALL_KEY) || ""; } catch {}
      if (!id) {
        id = nbRandomId("install");
        try { localStorage.setItem(NB_TELEMETRY_INSTALL_KEY, id); } catch {}
      }
      resolve(id);
    }
  });
}
function getNotebookSessionId() {
  const now = Date.now();
  let state = null;
  try { state = JSON.parse(localStorage.getItem(NB_TELEMETRY_SESSION_KEY) || "null"); } catch {}
  if (!state || !state.id || now - Number(state.lastSeen || 0) > NB_TELEMETRY_SESSION_TTL_MS) {
    state = { id: nbRandomId("session"), lastSeen: now };
  } else {
    state.lastSeen = now;
  }
  try { localStorage.setItem(NB_TELEMETRY_SESSION_KEY, JSON.stringify(state)); } catch {}
  return state.id;
}
function getNotebookEnv() {
  let browser = "", os = "";
  try {
    const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
    const main = brands.find((b) => /Chrome|Edge|Brave|Opera|Arc|Firefox/i.test(b.brand));
    browser = (main && main.brand) || (/Edg\//i.test(navigator.userAgent) ? "Edge" : "Chrome");
  } catch { browser = "Chrome"; }
  try {
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/mac/i.test(p)) os = "macOS";
    else if (/win/i.test(p)) os = "Windows";
    else if (/linux/i.test(p)) os = "Linux";
  } catch {}
  let extensionId = "";
  try { extensionId = (chrome.runtime && chrome.runtime.id) || ""; } catch {}
  return {
    app_version: "notebook-1",
    extension_id: extensionId,
    browser: browser || "Unknown",
    os: os || "Unknown",
    locale: navigator.language || "",
  };
}
async function notebookTelemetry(eventName, properties) {
  try {
    const installId = await getNotebookInstallId();
    const body = {
      event_name: eventName,
      anonymous_install_id: installId,
      app_session_id: getNotebookSessionId(),
      surface: "notebook",
      properties: { ...getNotebookEnv(), ...(properties || {}) },
    };
    await fetch(`${API_BASE}/telemetry/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch { /* fire-and-forget */ }
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

function offlineBackendCalloutHtml() {
  if (isWindowsPlatform()) {
    return `<em>知识库助手还没启动？</em><br><span class="kb-nb-mono-soft">.\\start.ps1</span>`;
  }
  return `<em>后端 agent_api.py 没启动？</em><br><span class="kb-nb-mono-soft">cd backend && python3 agent_api.py</span>`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function md(text) {
  if (typeof marked === "undefined") return escapeHtml(text || "").replace(/\n/g, "<br>");
  return marked.parse(text || "");
}

function toast(msg) {
  const el = $("kb-nb-toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

function fmtTimeAgo(iso) {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.max(1, Math.round(ms / 1000));
  if (s < 60) return s + " 秒前";
  if (s < 3600) return Math.round(s/60) + " 分钟前";
  if (s < 86400) return Math.round(s/3600) + " 小时前";
  if (s < 86400*30) return Math.round(s/86400) + " 天前";
  return new Date(iso).toLocaleDateString("zh", {month:"numeric",day:"numeric"});
}

function fmtClock(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const pad = n => String(n).padStart(2,"0");
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
}

function fmtDateShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return (d.getMonth()+1) + "-" + String(d.getDate()).padStart(2,"0");
}

const SEEN_AGENT_REPLY_KEY = "kb_nb_seen_agent_reply_ids_v1";
const SEEN_AGENT_REPLY_BASELINE_KEY = "kb_nb_seen_agent_reply_baseline_v1";
let _diaryUnreadReplyIds = [];
let _diaryBaselineInitializedNow = false;

function readSeenAgentReplyIds() {
  try {
    const arr = JSON.parse(localStorage.getItem(SEEN_AGENT_REPLY_KEY) || "[]");
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch {
    return new Set();
  }
}

function writeSeenAgentReplyIds(ids) {
  localStorage.setItem(SEEN_AGENT_REPLY_KEY, JSON.stringify(Array.from(ids)));
}

function ensureAgentReplySeenBaseline(replyIds) {
  _diaryBaselineInitializedNow = false;
  if (localStorage.getItem(SEEN_AGENT_REPLY_BASELINE_KEY)) return readSeenAgentReplyIds();
  const seen = readSeenAgentReplyIds();
  replyIds.filter(id => id !== undefined && id !== null).map(String).forEach(id => seen.add(id));
  writeSeenAgentReplyIds(seen);
  localStorage.setItem(SEEN_AGENT_REPLY_BASELINE_KEY, new Date().toISOString());
  _diaryBaselineInitializedNow = true;
  return seen;
}

function markAgentRepliesSeen(ids) {
  const normalized = (Array.isArray(ids) ? ids : [ids]).filter(id => id !== undefined && id !== null).map(String);
  if (!normalized.length) return;
  const seen = readSeenAgentReplyIds();
  normalized.forEach(id => seen.add(id));
  writeSeenAgentReplyIds(seen);
  normalized.forEach(id => {
    const item = document.querySelector(`[data-agent-reply-id="${CSS.escape(id)}"]`);
    if (item) {
      item.classList.remove("unread");
      const badge = item.querySelector(".kb-nb-diary-unread-tag");
      if (badge) badge.remove();
      const btn = item.querySelector("[data-read-reply]");
      if (btn) {
        btn.textContent = "已读";
        btn.disabled = true;
      }
    }
  });
  _diaryUnreadReplyIds = _diaryUnreadReplyIds.filter(id => !normalized.includes(String(id)));
  updateDiaryUnreadNotice();
}

function scrollToFirstUnreadReply() {
  const firstId = _diaryUnreadReplyIds[0];
  if (!firstId) return;
  switchTab("diary");
  setTimeout(() => {
    const item = document.querySelector(`[data-agent-reply-id="${CSS.escape(String(firstId))}"]`);
    if (item) {
      item.scrollIntoView({behavior: "smooth", block: "center"});
      item.classList.add("flash");
      setTimeout(() => item.classList.remove("flash"), 900);
    }
  }, 80);
}

function updateDiaryUnreadNotice() {
  const notice = $("kb-nb-diary-unread-notice");
  const count = _diaryUnreadReplyIds.length;
  const diaryNav = document.querySelector('.kb-nb-nav-item[data-tab="diary"]');
  const diaryCount = $("kb-nb-count-diary");
  const aiBanner = $("kb-nb-ai-return-banner");

  if (diaryNav) diaryNav.classList.toggle("has-unread", count > 0);
  if (diaryCount) {
    if (count > 0) {
      diaryCount.textContent = "";
      diaryCount.classList.add("unread");
      diaryCount.title = `${count} 条未读 Margin 回复`;
    } else {
      diaryCount.textContent = diaryCount.dataset.total || diaryCount.textContent || "—";
      diaryCount.classList.remove("unread");
      diaryCount.title = "";
    }
  }
  if (aiBanner) {
    aiBanner.classList.toggle("kb-nb-hidden", count <= 0);
    if (count > 0) {
      aiBanner.innerHTML = `
        <div><strong>Margin 回来了</strong><span>${count} 条回复待看</span></div>
        <button data-open-unread-replies>查看</button>
      `;
    }
  }

  if (!notice) return;
  if (!count) {
    notice.innerHTML = _diaryBaselineInitializedNow
      ? `<span>已建立已读基线，之后新完成的 Margin 回复会在这里提醒</span>`
      : `<span>没有未读 Margin 回复</span>`;
    notice.classList.add("empty");
    return;
  }
  notice.classList.remove("empty");
  notice.innerHTML = `
    <span>${count} 条未读 Margin 回复</span>
    <span class="kb-nb-diary-unread-actions">
      <button data-jump-unread-reply>查看第一条</button>
      <button data-mark-all-ai-read>全部标为已读</button>
    </span>
  `;
}

async function api(path, opts = {}) {
  try {
    const r = await fetch(API_BASE + path, opts);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    console.error("[notebook] API error", path, e);
    throw e;
  }
}

// ─── Tab 切换 ───
function switchTab(tab) {
  document.querySelectorAll(".kb-nb-nav-item").forEach(a => {
    a.classList.toggle("active", a.dataset.tab === tab);
  });
  document.querySelectorAll(".kb-nb-page").forEach(p => {
    p.classList.toggle("kb-nb-hidden", p.id !== "kb-tab-" + tab);
  });
  // URL 同步
  if (location.hash !== "#" + tab) location.hash = tab;
  // 懒加载具体页
  if (tab === "chat") loadChat();
  if (tab === "thought-map") loadThoughtMap();
  if (tab === "thinking") loadThinking();
  if (tab === "diary") loadDiary();
  // 运营埋点：子页打开
  notebookTelemetry("notebook_opened", { notebook_route: tab });
}

document.querySelectorAll(".kb-nb-nav-item").forEach(a => {
  a.addEventListener("click", e => {
    e.preventDefault();
    switchTab(a.dataset.tab);
  });
});

$("kb-nb-ai-return-banner").addEventListener("click", e => {
  if (e.target.closest("[data-open-unread-replies]")) scrollToFirstUnreadReply();
});

// ─── 1. 顶部 overview + 底部 callout ───
let _notionImportStarted = false;

async function maybeImportFromNotion(data) {
  if (_notionImportStarted) return;
  if (!data || data.comment_count !== 0 || !data.notion_configured) return;
  _notionImportStarted = true;
  toast("正在从 Notion 备份导入旧数据…");
  try {
    const result = await api("/notebook/import-notion", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({limit: 1000}),
    });
    if (result.imported > 0) {
      toast(`已导入 ${result.imported} 条旧数据`);
      loadOverview();
      loadDiary();
    } else {
      toast("Notion 备份没有可导入的旧数据");
    }
  } catch (e) {
    console.warn("[notebook] import notion failed", e);
    toast("Notion 备份旧数据导入失败");
  }
}

async function loadOverview() {
  try {
    const data = await api("/notebook/overview");
    const sync = $("kb-nb-overview-sync");
    sync.textContent = `本地 ${fmtClock(data.latest_sync)} · ${data.comment_count} 条评注 · ${data.page_count} 篇文章`;
    $("kb-nb-overview-meta").textContent = `共 ${data.page_count} 篇 · 评注 ${data.comment_count} 条`;
    const diaryCount = $("kb-nb-count-diary");
    if (diaryCount) {
      diaryCount.dataset.total = data.comment_count || 0;
      if (!_diaryUnreadReplyIds.length) diaryCount.textContent = data.comment_count || 0;
    }
    // 底部 callout：用最新 thinking_summary 的 title 当一句话观察
    const callout = $("kb-nb-callout-body");
    if (data.latest_thinking) {
      callout.innerHTML = `<em>"${escapeHtml(data.latest_thinking.title)}"</em><br>` +
        `<span class="kb-nb-mono-soft">${fmtTimeAgo(data.latest_thinking.created_at)} · 完整在「最近你在想的事」</span>`;
    } else {
      callout.innerHTML = `<em>还没整理过你最近的思考。</em><br>` +
        `<span class="kb-nb-mono-soft">去「最近你在想的事」让我跑一次。</span>`;
    }
    // 计数
    $("kb-nb-count-rules").textContent = data.active_rules || 0;
    $("kb-nb-count-diary").textContent = data.comment_count || 0;
    maybeImportFromNotion(data);
  } catch (e) {
    $("kb-nb-overview-sync").textContent = "后端离线（localhost:8766）";
    $("kb-nb-callout-body").innerHTML = offlineBackendCalloutHtml();
  }
}

// ─── 2. 你 & 项目 ───
async function loadProfile() {
  // 先加载完整 markdown（折叠展示）
  try {
    const data = await api("/notebook/profile");
    const profileEl = $("kb-nb-profile-md");
    const projectEl = $("kb-nb-project-md");
    if (data.user_profile_md && data.user_profile_md.trim()) {
      profileEl.innerHTML = md(data.user_profile_md);
    } else {
      profileEl.innerHTML = `<div class="kb-nb-empty">user_profile.md 还是空的。</div>`;
    }
    if (data.project_context_md && data.project_context_md.trim()) {
      projectEl.innerHTML = md(data.project_context_md);
    } else {
      projectEl.innerHTML = `<div class="kb-nb-empty">project_context.md 还是空的。</div>`;
    }
  } catch (e) {
    $("kb-nb-profile-md").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
    $("kb-nb-project-md").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
  // 然后看有没有 curated 缓存
  loadProfileCurated();
  loadMemoryMap();
}

// Curated 缓存：localStorage，24h
const CURATED_TTL_MS = 24 * 60 * 60 * 1000;

function readCuratedCache(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.ts || Date.now() - obj.ts > CURATED_TTL_MS) return null;
    return obj;
  } catch { return null; }
}
function writeCuratedCache(key, data) {
  try { localStorage.setItem(key, JSON.stringify({ ts: Date.now(), data })); } catch {}
}
function clearCuratedCache(key) {
  try { localStorage.removeItem(key); } catch {}
}

function renderProfileCurated(data, ts) {
  const box = $("kb-nb-profile-curated");
  const f = data.fields || {};
  const ageStr = ts ? fmtTimeAgo(new Date(ts).toISOString()) : "刚刚";
  box.innerHTML = `
    <div class="kb-nb-profile-oneliner">
      <div class="kb-nb-profile-oneliner-text">${escapeHtml(data.one_liner || "")}</div>
      <div class="kb-nb-profile-oneliner-meta">
        <span>${escapeHtml(data.since_last_check || "")}</span>
        <button class="kb-nb-profile-oneliner-refresh" id="kb-nb-profile-recurate">重新整理 · 上次 ${ageStr}</button>
      </div>
    </div>
    <div class="kb-nb-profile-fields">
      <div class="kb-nb-profile-field">
        <div class="kb-nb-profile-field-label">身份</div>
        <div class="kb-nb-profile-field-value">${escapeHtml(f.identity || "—")}</div>
      </div>
      <div class="kb-nb-profile-field">
        <div class="kb-nb-profile-field-label">当前项目</div>
        <div class="kb-nb-profile-field-value">${escapeHtml(f.current_project || "—")}</div>
      </div>
      <div class="kb-nb-profile-field">
        <div class="kb-nb-profile-field-label">北极星</div>
        <div class="kb-nb-profile-field-value">${escapeHtml(f.north_star || "—")}</div>
      </div>
      <div class="kb-nb-profile-field">
        <div class="kb-nb-profile-field-label">悬而未决</div>
        <div class="kb-nb-profile-field-value">${escapeHtml(f.pending || "—")}</div>
      </div>
    </div>
  `;
  const btn = document.getElementById("kb-nb-profile-recurate");
  if (btn) btn.addEventListener("click", () => triggerProfileCurated(true));
}

async function loadProfileCurated() {
  // Profile 状态卡已迁到 DB；清理旧 localStorage 缓存，避免换设备/换浏览器不一致
  clearCuratedCache("kb_nb_curated_profile");

  try {
    const data = await api("/notebook/profile/snapshot");
    if (data && data._status === "ok") {
      const ts = data.latest_generation && data.latest_generation.created_at
        ? new Date(data.latest_generation.created_at).getTime()
        : Date.now();
      renderProfileCurated(data, ts);
      return;
    }
  } catch (e) {
    console.warn("[notebook] load profile snapshot failed", e);
  }
  // 没 generation：保留 empty 状态等用户点击
  const btn = document.getElementById("kb-nb-profile-curate-btn");
  if (btn) {
    btn.onclick = () => triggerProfileCurated(false);
  }
}

async function triggerProfileCurated(isRefresh) {
  const box = $("kb-nb-profile-curated");
  box.innerHTML = `<div class="kb-nb-curated-running">Margin 正在整理你的最近批注和资料…</div>`;
  try {
    const data = await api("/notebook/profile/curated", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    if (data._status !== "ok") {
      box.innerHTML = `
        <div class="kb-nb-curated-empty">
          <div class="kb-nb-mono-soft">数据不足</div>
          <div style="margin-top:6px;color:var(--kb-ink-mute);">${escapeHtml(data._message || "user_profile.md / project_context.md 还是空的")}</div>
        </div>`;
      return;
    }
    renderProfileCurated(data, Date.now());
    toast(isRefresh ? "重新整理完成" : "整理完成");
  } catch (e) {
    box.innerHTML = `
      <div class="kb-nb-curated-failed">
        <div style="font-weight:500;margin-bottom:6px;">这次没整理出来</div>
        <div style="font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--kb-ink-mute);">${escapeHtml((e && e.message) || "未知错误")}</div>
        <button class="kb-nb-soft-btn" id="kb-nb-profile-retry" style="margin-top:10px;">再试一次 →</button>
      </div>`;
    const r = document.getElementById("kb-nb-profile-retry");
    if (r) r.addEventListener("click", () => triggerProfileCurated(isRefresh));
  }
}

// ─── 3. 养成的习惯（rules）───
let _rulesAllCache = null; // 存全量 rules，curated 渲染要查 rule_id 对应原文

async function loadRules() {
  try {
    const data = await api("/notebook/rules");
    _rulesAllCache = data.active.concat(data.archived || []);
    $("kb-nb-rules-week").textContent = data.stats.week_new ?? 0;
    $("kb-nb-rules-active").textContent = data.stats.active_count ?? 0;
    $("kb-nb-rules-hit").textContent = data.stats.hit_rate == null ? "—" : (Math.round(data.stats.hit_rate * 100) + "%");
    const list = $("kb-nb-rules-list");
    if (!data.active.length) {
      list.innerHTML = `<div class="kb-nb-empty">Margin 还没从你的反馈里学到任何工作方式。<br>批注、纠正它的回答，它会开始记。</div>`;
    } else {
      list.innerHTML = data.active.map(r => renderRule(r)).join("");
    }
  } catch (e) {
    $("kb-nb-rules-list").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
  // 然后看 curated
  loadRulesCurated();
}

function _findRuleById(rid) {
  if (!_rulesAllCache) return null;
  return _rulesAllCache.find(r => r.id === rid) || null;
}

function renderRulesCurated(data, ts) {
  const box = $("kb-nb-rules-curated");
  const ageStr = ts ? fmtTimeAgo(new Date(ts).toISOString()) : "刚刚";
  const skills = data.skills || [];
  const skillsHtml = skills.map(s => {
    const ids = s.evidence_rule_ids || [];
    const refsHtml = ids.map(id => `<a class="kb-nb-cref" data-rid="${escapeHtml(id)}" href="#${escapeHtml(id)}">${escapeHtml(id)}</a>`).join("");
    return `
      <div class="kb-nb-skill-card">
        <div class="kb-nb-skill-name">${escapeHtml(s.name || "")}</div>
        <div class="kb-nb-skill-desc">${escapeHtml(s.description || "")}</div>
        <div class="kb-nb-skill-evidence">
          <span class="kb-nb-skill-evidence-label">来自 ${ids.length} 条反馈：</span>
          ${refsHtml}
        </div>
      </div>
    `;
  }).join("");

  const uncategorizedCount = (data.uncategorized_rule_ids || []).length;
  const skillCount = skills.length;
  const newItems = Number(data._new_items_since_generation || 0);
  let statusText = data._message || "";
  if (!statusText && data._stale_reason && newItems > 0) {
    statusText = `又有 ${newItems} 条新反馈可整理，当前仍显示上一版。`;
  }
  const statusHtml = statusText
    ? `<p class="kb-nb-curated-status">${escapeHtml(statusText)}</p>`
    : "";
  const sourceCount = data._source_count ?? data._total_rules ?? 0;
  box.innerHTML = `
    <div class="kb-nb-skills-header">
      <h3 class="kb-nb-skills-title">我正在养成的 ${skillCount} 个工作方式</h3>
      <button class="kb-nb-profile-oneliner-refresh" id="kb-nb-rules-recurate">重新提炼 · 上次 ${ageStr}</button>
    </div>
    <p class="kb-nb-skills-subtitle">这是我作为你的协作者在长出的能力 — 来自你 ${sourceCount} 条具体反馈。</p>
    ${statusHtml}
    <div class="kb-nb-skills-list">${skillsHtml || '<div class="kb-nb-empty">还没识别出工作方式，再批注几次让我学到更多</div>'}</div>
    ${uncategorizedCount > 0 ? `
      <details class="kb-nb-md-details" style="margin-top:14px;">
        <summary class="kb-nb-md-details-summary">还有 ${uncategorizedCount} 条暂未归类的零散规则 ↓</summary>
        <div class="kb-nb-rules-groups" style="margin-top:8px;">
          ${(data.uncategorized_rule_ids || []).map(rid => {
            const orig = _findRuleById(rid);
            return `<div class="kb-nb-rules-group">
              <div class="kb-nb-group-ids">${escapeHtml(rid)}</div>
              ${orig ? `<div class="kb-nb-group-summary">${escapeHtml(orig.rule || "")}</div>` : ""}
            </div>`;
          }).join("")}
        </div>
      </details>
    ` : ""}
  `;
  const btn = document.getElementById("kb-nb-rules-recurate");
  if (btn) btn.addEventListener("click", () => triggerRulesCurated(true));
  // 给 evidence rule_id 加 hover 卡（复用 thinking 的浮卡）
  box.querySelectorAll(".kb-nb-cref[data-rid]").forEach(a => {
    a.addEventListener("mouseenter", () => showRuleHoverFor(a));
    a.addEventListener("mouseleave", () => hideCrefHover());
    a.addEventListener("click", e => e.preventDefault());
  });
}

// 复用 thinking 的浮卡显示 rule 详情
function showRuleHoverFor(anchor) {
  const rid = anchor.dataset.rid;
  const r = _findRuleById(rid);
  const el = ensureCrefHover();
  const rect = anchor.getBoundingClientRect();
  el.style.left = (rect.left + window.scrollX) + "px";
  el.style.top = (rect.bottom + window.scrollY + 6) + "px";
  if (!r) {
    el.innerHTML = `<div class="kb-nb-cref-loading">${escapeHtml(rid)} 没找到</div>`;
  } else {
    const src = r.source || r.created_at || "";
    el.innerHTML = `
      <div class="kb-nb-cref-meta">${escapeHtml(rid)} · ${escapeHtml(src)} · scope=${escapeHtml(r.scope || "all")}</div>
      <div class="kb-nb-cref-body">${escapeHtml(r.rule || "")}</div>
    `;
  }
  el.style.display = "block";
}

async function loadRulesCurated() {
  // M3.0 范围 B：从 DB 读 active skills（持久化），不再用 localStorage
  // localStorage 只作为旧缓存清理
  clearCuratedCache("kb_nb_curated_rules");

  try {
    const data = await api("/notebook/skills");
    if (data && data._status === "ok" && Array.isArray(data.skills) && data.skills.length > 0) {
      // 把 latest_generation 的时间戳作为 ts 传给渲染函数
      const ts = data.latest_generation && data.latest_generation.created_at
        ? new Date(data.latest_generation.created_at).getTime()
        : Date.now();
      renderRulesCurated(data, ts);
      return;
    }
  } catch (e) {
    console.warn("[notebook] load skills failed", e);
  }

  // 无 generation 或 DB 读失败 → 显示"让 Margin 整理一稿"按钮
  const btn = document.getElementById("kb-nb-rules-curate-btn");
  if (btn) btn.onclick = () => triggerRulesCurated(false);
}

async function triggerRulesCurated(isRefresh) {
  const box = $("kb-nb-rules-curated");
  box.innerHTML = `<div class="kb-nb-curated-running">Margin 正在把零散反馈提炼成工作方式…</div>`;
  try {
    const data = await api("/notebook/rules/curated", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    if (data._status !== "ok") {
      box.innerHTML = `
        <div class="kb-nb-curated-empty">
          <div class="kb-nb-mono-soft">${escapeHtml(data._status || "数据不足")}</div>
          <div style="margin-top:6px;color:var(--kb-ink-mute);">${escapeHtml(data._message || "")}</div>
        </div>`;
      return;
    }
    // M3.0 范围 B：后端已写 DB，前端不再 localStorage
    const ts = data._generation_created_at
      ? new Date(data._generation_created_at).getTime()
      : Date.now();
    renderRulesCurated(data, ts);
    const refreshStatus = data._refresh_status || (data._stale_rules_source ? "kept_previous" : "generated_new");
    const toastText = refreshStatus === "generated_new"
      ? (isRefresh ? "已整理成新版" : "已整理一版")
      : "新证据不足，已保留上一版";
    toast(toastText);
  } catch (e) {
    box.innerHTML = `
      <div class="kb-nb-curated-failed">
        <div style="font-weight:500;margin-bottom:6px;">这次没挑出来</div>
        <div style="font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--kb-ink-mute);">${escapeHtml((e && e.message) || "未知错误")}</div>
        <button class="kb-nb-soft-btn" id="kb-nb-rules-retry" style="margin-top:10px;">再试一次 →</button>
      </div>`;
    const r = document.getElementById("kb-nb-rules-retry");
    if (r) r.addEventListener("click", () => triggerRulesCurated(isRefresh));
  }
}

function tagFromScope(scope) {
  // 现有 learned_rules.json 的 scope: 'all' | 'role:researcher' 等
  if (!scope || scope === "all") return "RULE";
  if (scope.startsWith("role:")) return scope.replace("role:", "").toUpperCase();
  return "RULE";
}

function renderRule(r) {
  const tag = tagFromScope(r.scope);
  const id = (r.id || "").toString();
  const created = r.created_at || "";
  const lastUsed = r.last_used_at || created;
  const sourceText = r.source ? r.source : (created ? `自动提取 ${created}` : "");
  return `
    <div class="kb-nb-rule">
      <span class="kb-nb-rule-tag" title="${escapeHtml(r.scope || 'all')}">${escapeHtml(tag)}</span>
      <div>
        <div class="kb-nb-rule-body">${escapeHtml(r.rule || "")}</div>
        <div class="kb-nb-rule-source">${id ? `#${escapeHtml(id)} · ` : ""}${escapeHtml(sourceText)}</div>
      </div>
      <div class="kb-nb-rule-meta">
        ${lastUsed !== created ? `用过 · ${fmtTimeAgo(lastUsed)}` : "<span style='opacity:0.5'>未触发</span>"}
        <br>
        <span style="opacity:0.7">命中统计待启用</span>
      </div>
    </div>
  `;
}

// ─── 4. 最近你在想的事 ───
let _thinkingPolling = null;

async function loadThinking() {
  try {
    const data = await api("/notebook/thinking");
    $("kb-nb-count-thinking").textContent = data.archived_count + (data.active ? 1 : 0);
    renderThinking(data);
    // 如果数据库里完全没有 thinking_summary 且没有 running job，自动 queue 一次
    if (!data.active && !data.running_job) {
      requestThinking("first_open");
    }
    // 如果有 running job，开始轮询
    if (data.running_job) startThinkingPolling(data.running_job.id);
  } catch (e) {
    $("kb-nb-thinking-active").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
}

// comment 详情缓存（hover 时按需 fetch）
const _commentCache = new Map();
const _pageCache = new Map();
async function fetchComment(id) {
  if (_commentCache.has(id)) return _commentCache.get(id);
  try {
    const c = await api("/comments/" + id);
    _commentCache.set(id, c);
    return c;
  } catch {
    _commentCache.set(id, null);
    return null;
  }
}
async function fetchPageCache(id) {
  if (_pageCache.has(id)) return _pageCache.get(id);
  try {
    const p = await api("/notebook/page-cache/" + id);
    _pageCache.set(id, p);
    return p;
  } catch {
    _pageCache.set(id, null);
    return null;
  }
}

// 把渲染后的 HTML 里 [c#NNN] / [p#NNN] 文本节点替换为可点击锚点
function activateCommentRefs(rootEl) {
  const re = /\[(c|p)#(\d+)\]/g;
  const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT);
  const targets = [];
  let n;
  while ((n = walker.nextNode())) {
    if (re.test(n.nodeValue)) targets.push(n);
    re.lastIndex = 0;
  }
  for (const node of targets) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = node.nodeValue;
    const matches = [...text.matchAll(re)];
    for (const m of matches) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const kind = m[1];
      const id = parseInt(m[2], 10);
      const a = document.createElement("a");
      a.className = "kb-nb-cref";
      if (kind === "p") {
        a.dataset.pid = String(id);
        a.href = "#p" + id;
      } else {
        a.dataset.cid = String(id);
        a.href = "#c" + id;
      }
      a.textContent = kind + "#" + id;
      frag.appendChild(a);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  }
}

// 一个共享的 hover 卡片（避免每个引用一个）
let _crefHoverEl = null;
function ensureCrefHover() {
  if (_crefHoverEl) return _crefHoverEl;
  _crefHoverEl = document.createElement("div");
  _crefHoverEl.className = "kb-nb-cref-hover";
  _crefHoverEl.style.display = "none";
  document.body.appendChild(_crefHoverEl);
  _crefHoverEl.addEventListener("mouseenter", () => { _crefHoverEl._keep = true; });
  _crefHoverEl.addEventListener("mouseleave", () => { _crefHoverEl._keep = false; hideCrefHover(); });
  return _crefHoverEl;
}
function hideCrefHover() {
  if (!_crefHoverEl) return;
  setTimeout(() => {
    if (_crefHoverEl && !_crefHoverEl._keep) _crefHoverEl.style.display = "none";
  }, 120);
}
async function showCrefHoverFor(anchor) {
  const isPageRef = !!anchor.dataset.pid;
  const id = parseInt(isPageRef ? anchor.dataset.pid : anchor.dataset.cid, 10);
  const el = ensureCrefHover();
  const rect = anchor.getBoundingClientRect();
  el.style.left = (rect.left + window.scrollX) + "px";
  el.style.top = (rect.bottom + window.scrollY + 6) + "px";
  el.innerHTML = `<div class="kb-nb-cref-loading">读取 ${isPageRef ? "p" : "c"}#${id}…</div>`;
  el.style.display = "block";
  if (isPageRef) {
    const p = await fetchPageCache(id);
    if (!p || _crefHoverEl !== el) return;
    if (el.style.display === "none") return;
    const t = p.updated_at ? fmtDateShort(p.updated_at) + " · " + fmtClock(p.updated_at) : "";
    const preview = (p.full_text_preview || p.summary || "").replace(/\s+/g, " ").trim();
    const event = (p.exposure_events || [])[0];
    const evidence = event ? `${event.source_type || "seen"} · ${event.capture_reason || ""}` : "page_cache";
    el.innerHTML = `
      <div class="kb-nb-cref-meta">p#${id} · ${escapeHtml(t)} · exposure · ${escapeHtml(evidence)}</div>
      <div class="kb-nb-cref-quote">《${escapeHtml((p.page_title || "未命名").slice(0, 70))}》</div>
      <div class="kb-nb-cref-body">${escapeHtml(preview.slice(0, 520))}${preview.length > 520 ? "…" : ""}</div>
      <div class="kb-nb-cref-foot">
        <a href="${escapeHtml(p.page_url || "#")}" target="_blank" rel="noopener">打开来源页 ↗</a>
      </div>
    `;
    return;
  }
  const c = await fetchComment(id);
  if (!c || _crefHoverEl !== el) return;
  // 如果鼠标已经移走，且没 keep，就别弹
  if (el.style.display === "none") return;
  const t = c.created_at ? fmtDateShort(c.created_at) + " · " + fmtClock(c.created_at) : "";
  const excerpt = (c.selected_text || "").trim();
  el.innerHTML = `
    <div class="kb-nb-cref-meta">c#${id} · ${escapeHtml(t)} · 《${escapeHtml((c.page_title || "未命名").slice(0, 40))}》</div>
    ${excerpt ? `<div class="kb-nb-cref-quote">"${escapeHtml(excerpt.slice(0, 200))}${excerpt.length > 200 ? "…" : ""}"</div>` : ""}
    <div class="kb-nb-cref-body">${escapeHtml((c.comment || "").slice(0, 360))}${(c.comment || "").length > 360 ? "…" : ""}</div>
    <div class="kb-nb-cref-foot">
      <a href="${escapeHtml(c.page_url || "#")}" target="_blank" rel="noopener">打开来源页 ↗</a>
    </div>
  `;
}

function bindCrefAnchors(root) {
  if (!root) return;
  root.querySelectorAll(".kb-nb-cref").forEach(a => {
    a.addEventListener("mouseenter", () => showCrefHoverFor(a));
    a.addEventListener("mouseleave", () => hideCrefHover());
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      const isPageRef = !!a.dataset.pid;
      const id = parseInt(isPageRef ? a.dataset.pid : a.dataset.cid, 10);
      const item = isPageRef ? await fetchPageCache(id) : await fetchComment(id);
      if (item && item.page_url) {
        window.open(item.page_url, "_blank", "noopener");
      } else {
        toast((isPageRef ? "p#" : "c#") + id + " 没找到 / 没有来源页");
      }
    });
  });
}

function renderThinking(data) {
  const box = $("kb-nb-thinking-active");
  // 优先展示 running 状态
  if (data.running_job) {
    box.innerHTML = `<div class="kb-nb-thinking-running">Margin 正在整理你最近的思考，本地运行可能需要 1–3 分钟…</div>`;
    return;
  }
  if (!data.active) {
    box.innerHTML = `<div class="kb-nb-empty">还没整理过你最近的思考。点上方"立刻整理一次"开始。</div>`;
    return;
  }
  const a = data.active;
  const evidence = (() => { try { return JSON.parse(a.evidence_comment_ids || "[]"); } catch { return []; } })();
  box.innerHTML = `
    <div class="kb-nb-thinking-status">
      <span>${fmtTimeAgo(a.created_at)}</span>
      <span>·</span>
      <span>覆盖 ${fmtDateShort(a.window_start)} – ${fmtDateShort(a.window_end)}</span>
      <span>·</span>
      <span>基于 ${a.comments_since_last || "?"} 条批注</span>
    </div>
    <h3 class="kb-nb-thinking-title">${escapeHtml(a.title || "")}</h3>
    <div class="kb-nb-md" id="kb-nb-thinking-md">${md(a.synthesis_md || "")}</div>
    ${evidence.length ? `
      <div class="kb-nb-thinking-evidence">
        <span class="kb-nb-mono-soft">引用 ${evidence.length} 条 ·</span>
        ${evidence.map(id => `<a href="#c${id}" class="kb-nb-cref" data-cid="${id}">c#${id}</a>`).join("")}
      </div>
    ` : ""}
  `;
  // 把正文里的 [c#NNN] 也变成可点击锚点
  const mdBox = document.getElementById("kb-nb-thinking-md");
  if (mdBox) activateCommentRefs(mdBox);
  bindCrefAnchors(box);
}

// ─── 4.1 Project / Question / Theme Map V0 ───
let _memoryMapPromise = null;

async function loadMemoryMap(force) {
  if (!_memoryMapPromise || force) {
    _memoryMapPromise = api("/notebook/memory-map");
  }
  try {
    const data = await _memoryMapPromise;
    renderMemoryMap(data);
  } catch (e) {
    ["kb-nb-project-map", "kb-nb-question-map", "kb-nb-theme-map"].forEach(id => {
      const el = $(id);
      if (el) el.innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
    });
  }
}

async function refreshProjectMap() {
  const btn = $("kb-nb-project-refresh");
  const box = $("kb-nb-project-map");
  if (btn) btn.disabled = true;
  if (box) box.innerHTML = `<div class="kb-nb-curated-running">Margin 正在读取最新项目线索…</div>`;
  try {
    await loadMemoryMap(true);
    toast("已读取最新项目线索");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function refsHtml(ids) {
  const arr = Array.isArray(ids) ? ids : [];
  if (!arr.length) return "";
  return `<span class="kb-nb-map-refs">${arr.slice(0, 5).map(id =>
    `<a href="#c${id}" class="kb-nb-cref" data-cid="${id}">c#${id}</a>`
  ).join("")}</span>`;
}

function renderMemoryMap(data) {
  const projects = data.projects || [];
  const questions = data.active_questions || [];
  const themes = data.themes || [];
  const profileCount = $("kb-nb-count-profile");
  if (profileCount) profileCount.textContent = projects.length || 0;

  const projectBox = $("kb-nb-project-map");
  if (projectBox) {
    if (!projects.length) {
      projectBox.innerHTML = `<div class="kb-nb-empty">还没有足够证据自动长出当前项目。</div>`;
    } else {
      projectBox.innerHTML = projects.slice(0, 5).map(p => {
        const q = (p.questions || []).slice(0, 3);
        const summaries = (p.summaries || []).slice(0, 2);
        return `
          <article class="kb-nb-project-card">
            <div class="kb-nb-project-card-head">
              <h3>${escapeHtml(p.name || "未命名项目候选")}</h3>
              <span>${escapeHtml(p.status || "active")} · ${Math.round((p.confidence || 0) * 100)}%</span>
            </div>
            <div class="kb-nb-project-meta">
              ${fmtTimeAgo(p.last_seen_at)} · 证据 ${p.evidence_count || 0} 条 ${refsHtml(p.evidence_comment_ids || [])}
            </div>
            ${summaries.length ? `<div class="kb-nb-project-summary">${summaries.map(s => `<p>${escapeHtml(s)}</p>`).join("")}</div>` : ""}
            ${q.length ? `<div class="kb-nb-project-questions">
              ${q.map(item => `<div>· ${escapeHtml(item.question || "")}</div>`).join("")}
            </div>` : ""}
            ${(p.themes || []).length ? `<div class="kb-nb-map-tags">${p.themes.slice(0, 5).map(t => `<span>${escapeHtml(t)}</span>`).join("")}</div>` : ""}
            <div class="kb-nb-project-note">${escapeHtml(p.note || "")}</div>
          </article>
        `;
      }).join("");
      bindCrefAnchors(projectBox);
    }
  }

  const questionBox = $("kb-nb-question-map");
  if (questionBox) {
    questionBox.innerHTML = questions.length ? questions.slice(0, 8).map(q => `
      <div class="kb-nb-map-item">
        <div class="kb-nb-map-item-title">${escapeHtml(q.question || "")}</div>
        <div class="kb-nb-map-item-meta">${escapeHtml(q.scope || "unknown")} · ${Math.round((q.signal_strength || 0) * 100)}% · ${fmtTimeAgo(q.created_at)} ${refsHtml(q.evidence_comment_ids || [])}</div>
      </div>
    `).join("") : `<div class="kb-nb-empty">暂时没有活跃问题。</div>`;
    bindCrefAnchors(questionBox);
  }

  const themeBox = $("kb-nb-theme-map");
  if (themeBox) {
    themeBox.innerHTML = themes.length ? themes.slice(0, 8).map(t => `
      <div class="kb-nb-map-item">
        <div class="kb-nb-map-item-title">${escapeHtml(t.theme || "")}</div>
        <div class="kb-nb-map-item-meta">${escapeHtml(t.trend || "active")} · intensity ${t.intensity || 0} · 证据 ${t.evidence_count || 0} · ${fmtTimeAgo(t.last_seen_at)} ${refsHtml(t.representative_comment_ids || [])}</div>
      </div>
    `).join("") : `<div class="kb-nb-empty">暂时没有主题热度。</div>`;
    bindCrefAnchors(themeBox);
  }
}

// ─── 4.2 思考地图：主线 / 旁支 / 新芽 / 降温 ───
let _thoughtMapPromise = null;
let _lastThoughtMapData = null;

async function loadThoughtMap(force) {
  if (!_thoughtMapPromise || force) {
    _thoughtMapPromise = Promise.allSettled([
      api("/notebook/thought-map"),
      api("/notebook/memory-map"),
    ]);
  }
  const box = $("kb-nb-thought-map");
  if (!box) return;
  try {
    const [thoughtResult, memoryResult] = await _thoughtMapPromise;
    if (thoughtResult.status !== "fulfilled") throw thoughtResult.reason;
    renderThoughtMap({
      ...thoughtResult.value,
      memory_map: memoryResult.status === "fulfilled" ? memoryResult.value : null,
    });
  } catch (e) {
    box.innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
}

async function refreshThoughtMap() {
  const btn = $("kb-nb-thought-map-refresh");
  const box = $("kb-nb-thought-map");
  if (btn) btn.disabled = true;
  if (box) box.innerHTML = `<div class="kb-nb-curated-running">Margin 正在读取最新思考线索…</div>`;
  try {
    await loadThoughtMap(true);
    toast("思考地图已刷新");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function sparklineSvg(values) {
  const nums = Array.isArray(values) && values.length ? values.map(v => Number(v) || 0) : [0,0,0,0,0,0];
  const max = Math.max(1, ...nums);
  const pts = nums.map((v, i) => {
    const x = 8 + i * (92 / Math.max(1, nums.length - 1));
    const y = 34 - (v / max) * 26;
    return `${x},${y}`;
  }).join(" ");
  const dots = nums.map((v, i) => {
    const x = 8 + i * (92 / Math.max(1, nums.length - 1));
    const y = 34 - (v / max) * 26;
    return `<circle cx="${x}" cy="${y}" r="1.8"></circle>`;
  }).join("");
  return `<svg class="kb-nb-thought-spark" viewBox="0 0 108 40" aria-hidden="true">
    <polyline points="${pts}"></polyline>${dots}
  </svg>`;
}

function laneLabel(lane) {
  return {
    mainline: "主线",
    merging: "合流",
    branch: "旁支",
    sprout: "新芽",
    occasional: "待观察",
    cooling: "降温",
  }[lane] || lane;
}

function trendText(trend) {
  return {
    rising: "升温",
    new: "新出现",
    cooling: "降温",
    steady: "稳定",
  }[trend] || trend;
}

function levelClass(level) {
  if (level === "高") return "high";
  if (level === "中") return "mid";
  return "low";
}

function thoughtScorePills(node) {
  const items = [
    ["意图", node.intent_strength || "低"],
    ["置信", node.confidence || "低"],
    ["持续", node.persistence || "低"],
    ["中心", node.centrality || "低"],
  ];
  return items.map(([label, value]) => `
    <span class="kb-nb-thought-pill ${levelClass(value)}">
      <b>${escapeHtml(label)}</b>${escapeHtml(value)}
    </span>
  `).join("");
}

function flattenThoughtNodes(lanes) {
  const order = ["mainline", "merging", "branch", "sprout", "occasional", "cooling"];
  return order.flatMap(k => Array.isArray(lanes?.[k]) ? lanes[k] : []);
}

function renderThoughtHeroSummary(lanes, observation, aha) {
  const main = (lanes.mainline || [])[0];
  const rising = [...(lanes.sprout || []), ...(lanes.merging || [])].filter(n => n.trend === "rising" || n.lane === "sprout").slice(0, 3);
  const cooling = lanes.cooling || [];
  const rows = [
    main ? { label: "主线仍是", value: main.label, note: "之后相关评论会优先带入这条上下文" } : null,
    rising.length ? { label: "最近升温", value: rising.map(n => n.label).join("、"), note: "后续会重点观察是否继续出现" } : null,
    cooling.length ? { label: "开始降温", value: cooling.map(n => n.label).join("、"), note: "降低解释优先级，但保留为背景" } : null,
  ].filter(Boolean);
  const headline = aha?.headline || observation || "";
  const read = aha?.read || "";
  const next = aha?.next_question || "";
  if (!rows.length && !headline) return "";
  return `
    ${headline ? `<h3>${escapeHtml(headline)}</h3>` : ""}
    ${read ? `<p>${escapeHtml(read)}</p>` : ""}
    ${next ? `<p class="kb-nb-thought-next">${escapeHtml(next)} ${refsHtml(aha?.evidence_comment_ids || [])}</p>` : ""}
    <div class="kb-nb-thought-summary">
      ${rows.map(item => `
        <div>
          <span>${escapeHtml(item.label)}</span>
          <b>${escapeHtml(item.value || "")}</b>
          <em>${escapeHtml(item.note || "")}</em>
        </div>
      `).join("")}
    </div>
  `;
}

function thoughtUsageText(node) {
  return {
    mainline: "之后相关评论会优先带上这条主线，避免把问题当成孤立提问。",
    merging: "会把它和主线放在一起理解，帮助判断你为什么问这件事。",
    branch: "作为背景线索保留；相关问题出现时再进入上下文。",
    sprout: "先重点观察后续是否继续出现，不马上写死成长期兴趣。",
    occasional: "先不放大解释；除非你继续追问，否则只当作一次技术/信息补全。",
    cooling: "降低优先级；必要时作为历史背景，不主动抢占解释方向。",
  }[node?.lane] || "作为一条待验证线索，后续根据你的行为继续校准。";
}

function thoughtCertaintyText(node) {
  const confidence = node?.confidence || "低";
  const intent = node?.intent_strength || "低";
  if (confidence === "高" && intent === "高") return "证据和行动信号都比较强。";
  if (confidence === "高") return "证据不少，但近期行动信号需要继续观察。";
  if (intent === "高") return "行动信号强，但证据还少。";
  return "证据还少，先不做强判断。";
}

function renderThoughtMapRow(node, activeId) {
  const active = node.id === activeId ? " active" : "";
  return `
    <button class="kb-nb-thought-row ${escapeHtml(node.lane || "")}${active}" data-thought-id="${escapeHtml(node.id)}" type="button">
      <span class="kb-nb-thought-row-title">${escapeHtml(node.label || "")}</span>
      <span class="kb-nb-thought-row-spark">${sparklineSvg(node.sparkline || [])}</span>
      <span class="kb-nb-thought-row-state">${laneLabel(node.lane)} · ${trendText(node.trend)}</span>
      <span class="kb-nb-thought-row-use">${escapeHtml(thoughtUsageText(node))}</span>
      <span class="kb-nb-thought-row-count">${node.evidence_count || 0}</span>
    </button>
  `;
}

function renderThoughtTrendList(nodes, activeId) {
  const items = nodes || [];
  return `
    <div class="kb-nb-thought-table">
      <div class="kb-nb-thought-table-head">
        <span>线索</span>
        <span>趋势</span>
        <span>状态</span>
        <span>之后回复会怎么用</span>
        <span>证据</span>
      </div>
      ${items.length ? items.map(n => renderThoughtMapRow(n, activeId)).join("") : `<div class="kb-nb-empty">暂时没有足够证据。</div>`}
    </div>
  `;
}

function questionTextForCluster(q) {
  return [q.question || "", q.page_title || ""].join(" ").toLowerCase();
}

const _betterQuestionActions = new Map();
const _betterQuestionPending = new Map();
let _commentBridgePromise = null;

const BETTER_QUESTION_STOP_TERMS = new Set([
  "如何", "是否", "什么", "哪些", "为什么", "怎么", "能否", "如果", "以及", "这个", "这些",
  "一个", "一种", "当前", "最终", "用户", "系统", "问题", "场景", "方式", "需要", "应该",
  "可以", "进行", "相关", "真实", "结合", "类似", "更多", "判断", "设计", "the", "and",
  "for", "with", "from", "into", "what", "why", "how", "should", "could",
]);

function stableHash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = ((h << 5) - h + text.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36);
}

function keywordTokensFromText(text, maxTokens = 18) {
  const raw = String(text || "").toLowerCase();
  const weighted = new Map();
  const add = (token, weight = 1) => {
    const t = String(token || "").trim();
    if (!t || t.length < 2 || BETTER_QUESTION_STOP_TERMS.has(t)) return;
    if (/^\d+$/.test(t)) return;
    weighted.set(t, (weighted.get(t) || 0) + weight);
  };

  (raw.match(/[a-z0-9][a-z0-9_-]{2,}/g) || []).forEach(t => add(t, 1.2));
  (raw.match(/[\u4e00-\u9fff]{2,}/g) || []).forEach(segment => {
    if (segment.length <= 6) {
      add(segment, 1.4);
      return;
    }
    for (let n = 2; n <= 4; n += 1) {
      for (let i = 0; i <= segment.length - n; i += 1) add(segment.slice(i, i + n), 1);
    }
  });

  return [...weighted.entries()]
    .sort((a, b) => b[1] - a[1] || b[0].length - a[0].length)
    .slice(0, maxTokens)
    .map(([token]) => token);
}

function questionKeywords(q) {
  return keywordTokensFromText(questionTextForCluster(q), 20);
}

function sharedTokenCount(a, b) {
  const bSet = new Set(b || []);
  return (a || []).filter(t => bSet.has(t)).length;
}

function evidenceOverlap(a, b) {
  const left = new Set(a?.evidence_comment_ids || []);
  return (b?.evidence_comment_ids || []).some(id => left.has(id));
}

function questionStrength(q) {
  return Number(q?.signal_strength || 0) + ((q?.scope === "project") ? 0.08 : 0);
}

function sortedQuestionMatches(items) {
  return [...items].sort((a, b) => {
    const strength = questionStrength(b) - questionStrength(a);
    if (strength) return strength;
    return String(b.created_at || "").localeCompare(String(a.created_at || ""));
  });
}

function commonTokensForQuestions(matches, max = 3) {
  const counts = new Map();
  (matches || []).forEach(q => {
    const seen = new Set(questionKeywords(q).slice(0, 12));
    seen.forEach(t => counts.set(t, (counts.get(t) || 0) + 1));
  });
  return [...counts.entries()]
    .filter(([, n]) => n >= 2)
    .sort((a, b) => b[1] - a[1] || b[0].length - a[0].length)
    .slice(0, max)
    .map(([t]) => t);
}

function compactTopic(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) return "这组问题";
  return value.length > 28 ? value.slice(0, 28) + "…" : value;
}

function buildBetterQuestions(topic, matches) {
  const t = compactTopic(topic);
  const hasProjectScope = matches.some(q => q.scope === "project");
  const highStrength = matches.some(q => Number(q.signal_strength || 0) >= 0.85);
  const prompts = [
    `这组问题背后的共同判断是什么，哪些证据已经支持它？`,
    `如果只推进一步，围绕「${t}」最应该补哪类证据？`,
    `这件事应该进入行动或项目，还是先继续观察？`,
  ];
  if (hasProjectScope) {
    prompts.unshift(`要把「${t}」推进成项目决策，还缺哪条正反证据？`);
  }
  if (highStrength) {
    prompts.splice(1, 0, `下次遇到相关材料时，我应该优先验证「${t}」里的哪个假设？`);
  }
  return [...new Set(prompts)].slice(0, 3);
}

function makeQuestionCluster(topic, matches, source) {
  const sorted = sortedQuestionMatches(matches).slice(0, 6);
  const common = commonTokensForQuestions(sorted);
  const displayTopic = topic || (common.length ? common.join(" / ") : sorted[0]?.question || "这组问题");
  return {
    id: `dynamic-${stableHash(`${source}:${displayTopic}:${sorted.map(q => q.id || q.comment_id).join(",")}`)}`,
    label: `围绕「${compactTopic(displayTopic)}」的问题正在成形`,
    topic: displayTopic,
    source,
    matches: sorted,
    better: buildBetterQuestions(displayTopic, sorted),
  };
}

function clusterQuestions(memoryMap) {
  const questions = (memoryMap?.active_questions || [])
    .filter(q => (q.question || "").trim())
    .map((q, idx) => ({...q, _idx: idx, _tokens: questionKeywords(q)}));
  const themes = (memoryMap?.themes || []).filter(t => (t.theme || "").trim());
  const clusters = [];
  const used = new Set();
  const keyFor = q => String(q.id || q.comment_id || q._idx);

  themes.slice(0, 10).forEach(theme => {
    const themeTokens = keywordTokensFromText(theme.theme || "", 16);
    const themeEvidence = new Set(theme.representative_comment_ids || []);
    const matches = questions.filter(q => {
      if (used.has(keyFor(q))) return false;
      const tokenHit = sharedTokenCount(q._tokens, themeTokens) >= 1;
      const evidenceHit = (q.evidence_comment_ids || []).some(id => themeEvidence.has(id));
      return tokenHit || evidenceHit;
    });
    if (matches.length >= 2) {
      const cluster = makeQuestionCluster(theme.theme, matches, "theme");
      clusters.push(cluster);
      cluster.matches.forEach(q => used.add(keyFor(q)));
    }
  });

  questions.forEach(seed => {
    if (used.has(keyFor(seed))) return;
    const matches = [seed];
    questions.forEach(candidate => {
      if (candidate === seed || used.has(keyFor(candidate))) return;
      const overlap = sharedTokenCount(seed._tokens, candidate._tokens);
      const strongLink = overlap >= 2 || (overlap >= 1 && (candidate.scope === seed.scope || evidenceOverlap(seed, candidate)));
      if (strongLink) matches.push(candidate);
    });
    if (matches.length >= 2) {
      const common = commonTokensForQuestions(matches);
      const cluster = makeQuestionCluster(common.join(" / "), matches, "question-overlap");
      clusters.push(cluster);
      cluster.matches.forEach(q => used.add(keyFor(q)));
    }
  });

  return clusters
    .sort((a, b) => {
      const score = questionStrength(sortedQuestionMatches(b.matches)[0]) - questionStrength(sortedQuestionMatches(a.matches)[0]);
      if (score) return score;
      return b.matches.length - a.matches.length;
    })
    .slice(0, 5);
}

function evidenceRefsForQuestion(q) {
  return (q.evidence_comment_ids || [])
    .slice(0, 4)
    .map(id => `[c#${id}]`)
    .join(" ");
}

function registerBetterQuestionAction(group, question, questionIndex) {
  const actionId = `${group.id}-${questionIndex}`;
  const matches = group.matches.slice(0, 4).map(q => ({
    question: q.question || "",
    evidence_comment_ids: q.evidence_comment_ids || [],
    page_title: q.page_title || "",
    scope: q.scope || "",
  }));
  _betterQuestionActions.set(actionId, {
    actionId,
    groupId: group.id,
    groupLabel: group.label,
    question,
    matches,
  });
  return actionId;
}

function betterQuestionAgentContext(action) {
  const surfaceLines = action.matches.map((q, idx) => {
    const refs = evidenceRefsForQuestion(q);
    const title = q.page_title ? ` · 来源：${q.page_title}` : "";
    return `${idx + 1}. ${q.question}${refs ? ` ${refs}` : ""}${title}`;
  }).join("\n");
  return [
    "我在 Notebook 的 Better Question 里点击了一个「更好的下一问」。请把它当作由一组历史批注/追问抽象出来的下一步行动，而不是孤立问题。",
    "",
    `## 更好的下一问\n${action.question}`,
    "",
    `## 它所属的底层问题\n${action.groupLabel}`,
    "",
    `## 表面上我反复在问\n${surfaceLines || "没有可展示的表面问题"}`,
    "",
    "## 回答策略",
    "- 优先使用这些 [c#] 证据和本机记忆上下文回答；引用证据时保留 [c#123]。",
    "- 先判断这个下一问为什么值得问，再给出能推进判断或行动的框架。",
    "- 如果需要外部事实才能回答完整，明确列出需要搜索验证的点，不要编。",
    "- 不要只复述问题；要把表面问题背后的共同张力说清楚。",
  ].join("\n");
}

function ensureCommentBridge() {
  if (window.kbCommentSystem?.askAIForQuestionExcerpt) return Promise.resolve(true);
  if (location.protocol !== "chrome-extension:") return Promise.resolve(false);
  if (_commentBridgePromise) return _commentBridgePromise;
  _commentBridgePromise = new Promise(resolve => {
    const script = document.createElement("script");
    script.src = "../content/index.js";
    script.onload = () => resolve(Boolean(window.kbCommentSystem?.askAIForQuestionExcerpt));
    script.onerror = () => resolve(false);
    document.documentElement.appendChild(script);
  });
  return _commentBridgePromise;
}

function renderBetterQuestionButton(group, question, index) {
  const actionId = registerBetterQuestionAction(group, question, index);
  return `
    <li>
      <button type="button" class="kb-nb-better-action" data-better-question="${escapeHtml(actionId)}">
        <span>${escapeHtml(question)}</span>
        <em>开问</em>
      </button>
    </li>
  `;
}

function renderBetterQuestions(memoryMap) {
  const clusters = clusterQuestions(memoryMap);
  _betterQuestionActions.clear();
  if (!clusters.length) {
    return `
      <div class="kb-nb-thought-better-empty">
        还没有足够证据合成更好的问题。系统不会把“最近问过什么”直接包装成萦绕。
      </div>
    `;
  }
  return `
    <div class="kb-nb-thought-better-list">
      ${clusters.slice(0, 3).map(group => `
        <article class="kb-nb-thought-better" data-better-group="${escapeHtml(group.id)}">
          <div class="kb-nb-page-mono">问题正在成形 · ${group.matches.length} 条表面信号</div>
          <h3>${escapeHtml(group.label)}</h3>
          <div class="kb-nb-thought-better-body">
            <div class="kb-nb-thought-better-main">
              <div class="kb-nb-thought-better-surface">
                <b>表面上你在问</b>
                <ul>
                  ${group.matches.slice(0, 4).map(q => `<li>${escapeHtml(q.question || "")} ${refsHtml(q.evidence_comment_ids || [])}</li>`).join("")}
                </ul>
              </div>
              <div class="kb-nb-thought-better-next">
                <b>更好的下一问</b>
                <ul class="kb-nb-thought-better-actions">
                  ${group.better.map((q, idx) => renderBetterQuestionButton(group, q, idx)).join("")}
                </ul>
              </div>
            </div>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

async function askBetterQuestion(actionId) {
  const action = _betterQuestionActions.get(actionId);
  if (!action) return;
  const article = document.querySelector(`[data-better-group="${CSS.escape(action.groupId)}"]`);
  const clicked = article?.querySelector(`[data-better-question="${CSS.escape(actionId)}"]`);
  if (!article || !clicked) return;
  const payload = {
    excerpt: action.question,
    comment: "请直接回答这句下一问。",
    contextText: betterQuestionAgentContext(action),
    source: "notebook_better_question",
  };
  article.querySelectorAll("[data-better-question]").forEach(btn => {
    btn.classList.toggle("active", btn === clicked);
  });
  clicked.disabled = true;
  const hasDirectBridge = await ensureCommentBridge();
  if (hasDirectBridge && window.kbCommentSystem?.askAIForQuestionExcerpt) {
    const localCommentId = window.kbCommentSystem.askAIForQuestionExcerpt(payload);
    clicked.disabled = false;
    toast(localCommentId ? "已在右侧评注里召唤 Margin" : "召唤 Margin 失败");
    return;
  }
  _betterQuestionPending.set(actionId, clicked);
  window.postMessage({
    __kb_action: BETTER_QUESTION_ASK_AI,
    actionId,
    payload,
  }, "*");
  setTimeout(() => {
    if (!_betterQuestionPending.has(actionId)) return;
    _betterQuestionPending.delete(actionId);
    clicked.disabled = false;
    toast("没有检测到评注插件，请刷新插件和当前页面");
  }, 1200);
}

function bindBetterQuestionActions(root) {
  root.querySelectorAll("[data-better-question]").forEach(btn => {
    btn.addEventListener("click", () => askBetterQuestion(btn.dataset.betterQuestion));
  });
}

window.addEventListener("message", e => {
  if (e.data?.__kb_action_result !== BETTER_QUESTION_ASK_AI) return;
  const actionId = e.data.actionId;
  const btn = _betterQuestionPending.get(actionId);
  if (btn) {
    btn.disabled = false;
    _betterQuestionPending.delete(actionId);
  }
  toast(e.data.ok ? "已在右侧评注里召唤 Margin" : "召唤 Margin 失败");
});

function renderThoughtNode(node) {
  const refs = refsHtml(node.evidence_comment_ids || []);
  const evidence = (node.evidence || []).slice(0, 3).map(e => {
    const raw = (e.raw_comment || e.comment || "").replace(/\s+/g, " ").trim();
    const terms = (e.matched_terms || []).slice(0, 3);
    const signals = (e.behavior?.labels || []).slice(0, 3);
    return `
    <div class="kb-nb-thought-evidence">
      <span>${escapeHtml(fmtDateShort(e.created_at))}</span>
      <b>${escapeHtml((e.page_title || "未命名").slice(0, 44))}</b>
      <p>${escapeHtml(e.interpretation || "")}</p>
      <div class="kb-nb-thought-evidence-tags">
        ${terms.map(t => `<em>${escapeHtml(t)}</em>`).join("")}
        ${signals.map(t => `<em>${escapeHtml(t)}</em>`).join("")}
      </div>
      ${raw ? `<details><summary>原始行为</summary><div>${escapeHtml(raw.slice(0, 260))}${raw.length > 260 ? "…" : ""}</div></details>` : ""}
    </div>
  `;
  }).join("");
  return `
    <article class="kb-nb-thought-node ${escapeHtml(node.lane || "")}">
      <div class="kb-nb-thought-node-main">
        <div class="kb-nb-thought-node-kicker">${laneLabel(node.lane)} · ${trendText(node.trend)}</div>
        <h3>${escapeHtml(node.label || "")}</h3>
        <p>${escapeHtml(node.read || "")}</p>
        <div class="kb-nb-thought-use">
          <b>之后回复会怎么用</b>
          <span>${escapeHtml(thoughtUsageText(node))}</span>
        </div>
        <div class="kb-nb-thought-meta">
          ${node.evidence_count || 0} 条证据 · ${node.distinct_day_count || 0} 天出现 · 最近 7 天 ${node.recent_count || 0} 条 ${refs}
        </div>
        <details class="kb-nb-thought-basis">
          <summary>判断依据</summary>
          <div class="kb-nb-thought-pills">${thoughtScorePills(node)}</div>
          <p>${escapeHtml(thoughtCertaintyText(node))}</p>
          ${node.possible_misread ? `<p>${escapeHtml(node.possible_misread)}</p>` : ""}
        </details>
      </div>
      <div class="kb-nb-thought-side">
        ${sparklineSvg(node.sparkline || [])}
      </div>
      ${evidence ? `<div class="kb-nb-thought-evidence-list">${evidence}</div>` : ""}
    </article>
  `;
}

function renderThoughtFocus(node) {
  const box = $("kb-nb-thought-focus");
  if (!box) return;
  if (!node) {
    box.innerHTML = `<div class="kb-nb-empty">选择一条线索查看详情。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="kb-nb-thought-focus-head">
      <div>
        <span class="kb-nb-page-mono">当前展开 · ${laneLabel(node.lane)} · ${trendText(node.trend)}</span>
        <h3>${escapeHtml(node.label || "")}</h3>
      </div>
      <div class="kb-nb-thought-focus-spark">${sparklineSvg(node.sparkline || [])}</div>
    </div>
    ${renderThoughtNode(node)}
  `;
  bindCrefAnchors(box);
}

function bindThoughtMapNodes(nodes) {
  document.querySelectorAll(".kb-nb-thought-row").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.thoughtId;
      const node = nodes.find(n => n.id === id);
      document.querySelectorAll(".kb-nb-thought-row").forEach(el => {
        el.classList.toggle("active", el.dataset.thoughtId === id);
      });
      renderThoughtFocus(node);
    });
  });
}

function bindThoughtTabs(root) {
  if (!root) return;
  const buttons = root.querySelectorAll("[data-thought-tab]");
  const panels = root.querySelectorAll("[data-thought-panel]");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.thoughtTab;
      buttons.forEach(item => {
        const active = item.dataset.thoughtTab === tab;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
      panels.forEach(panel => {
        panel.classList.toggle("active", panel.dataset.thoughtPanel === tab);
      });
    });
  });
}

function renderThoughtMap(data) {
  const box = $("kb-nb-thought-map");
  const lanes = data.lanes || {};
  const memory = data.memory_map || {};
  const nodes = flattenThoughtNodes(lanes);
  const active = (lanes.sprout || [])[0] || (lanes.mainline || [])[0] || nodes[0];
  _lastThoughtMapData = data;
  $("kb-nb-count-thought-map").textContent = data.stats?.node_count || 0;
  box.innerHTML = `
    <div class="kb-nb-thought-hero">
      <div>
        <div class="kb-nb-page-mono">${escapeHtml(data.window_label || `过去 ${data.window_days || 42} 天`)} · 本地批注证据</div>
        ${renderThoughtHeroSummary(lanes, data.observation, data.aha)}
        <p>外显记忆先看两件事：你围绕哪些主题在变化，以及这些表面问题背后有没有更好的下一问。</p>
      </div>
    </div>
    <div class="kb-nb-thought-tabs" role="tablist" aria-label="思考地图视图">
      <button class="active" type="button" role="tab" aria-selected="true" data-thought-tab="topic">
        <b>Topic</b>
        <span>主题线索与证据</span>
      </button>
      <button type="button" role="tab" aria-selected="false" data-thought-tab="better">
        <b>Better Question</b>
        <span>问题正在成形</span>
      </button>
    </div>
    <div class="kb-nb-thought-panel active" data-thought-panel="topic">
      <div class="kb-nb-thought-section-head">
        <span class="kb-nb-page-mono">主题线索</span>
        <p>先看哪些主题是主线、升温、待观察或降温；点一条线索，下方只展开这条线索的证据。</p>
      </div>
      ${renderThoughtTrendList(nodes, active?.id)}
      <div id="kb-nb-thought-focus" class="kb-nb-thought-focus"></div>
    </div>
    <div class="kb-nb-thought-panel" data-thought-panel="better">
      <div class="kb-nb-thought-section-head">
        <span class="kb-nb-page-mono">问题正在成形</span>
        <p>不是列出最近问题，而是把反复出现的表面问题抽象成更底层的思考，并给出更好的下一问。</p>
      </div>
      ${renderBetterQuestions(memory)}
    </div>
  `;
  bindCrefAnchors(box);
  bindThoughtTabs(box);
  bindBetterQuestionActions(box);
  bindThoughtMapNodes(nodes);
  renderThoughtFocus(active);
}

async function requestThinking(reason) {
  try {
    const r = await api("/jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ kind: "synthesize_thinking", payload: { trigger_reason: reason } })
    });
    toast(r.deduped ? "已经在整理了 · 等一下" : "已开始整理");
    startThinkingPolling(r.id);
  } catch (e) {
    toast("无法触发 · 检查后端");
  }
}

function startThinkingPolling(jobId) {
  if (_thinkingPolling) clearInterval(_thinkingPolling);
  // 立刻渲染 running
  renderThinking({ active: null, running_job: { id: jobId } });
  _thinkingPolling = setInterval(async () => {
    try {
      const job = await api("/jobs/" + jobId);
      if (job.status === "done") {
        clearInterval(_thinkingPolling); _thinkingPolling = null;
        loadThinking();
        loadOverview();
        toast("整理好了");
      } else if (job.status === "failed") {
        clearInterval(_thinkingPolling); _thinkingPolling = null;
        renderThinkingFailed(job);
        toast("整理失败 · 见下方提示");
      }
    } catch (e) {
      // 忽略，下次再轮
    }
  }, 3000);
}

function renderThinkingFailed(job) {
  const box = $("kb-nb-thinking-active");
  const errShort = (job.error || "未知错误").split("\n")[0].slice(0, 200);
  const attempts = job.attempts || 0;
  box.innerHTML = `
    <div class="kb-nb-thinking-status">
      <span style="color:var(--kb-terra);">整理失败</span>
      <span>·</span>
      <span>job#${job.id} · 重试 ${attempts}/3</span>
      <span>·</span>
      <span>${fmtTimeAgo(job.finished_at || job.created_at)}</span>
    </div>
    <h3 class="kb-nb-thinking-title" style="color:var(--kb-terra);">这次没整理出来</h3>
    <div class="kb-nb-md">
      <p><strong>错误：</strong><code>${escapeHtml(errShort)}</code></p>
      <p>常见原因：</p>
      <ul>
        <li>当前 LLM 后端不可用、超时或被限流</li>
        <li>JSON 解析失败（worker 已加多策略 fallback，但仍可能踩雷）</li>
        <li>本地 CLI 后端超时</li>
      </ul>
      <p>排查：<code>tail -50 backend/.logs/worker.log</code> + <code>tail backend/.logs/failures.jsonl</code></p>
    </div>
    <div style="margin-top:12px;">
      <button id="kb-nb-thinking-retry" class="kb-nb-soft-btn">再试一次 →</button>
    </div>
  `;
  const retry = document.getElementById("kb-nb-thinking-retry");
  if (retry) retry.addEventListener("click", () => requestThinking("user_request"));
}

const projectRefreshBtn = $("kb-nb-project-refresh");
if (projectRefreshBtn) projectRefreshBtn.addEventListener("click", refreshProjectMap);

const thoughtMapRefreshBtn = $("kb-nb-thought-map-refresh");
if (thoughtMapRefreshBtn) thoughtMapRefreshBtn.addEventListener("click", refreshThoughtMap);

$("kb-nb-thinking-refresh").addEventListener("click", () => requestThinking("user_request"));

// ─── 5. 问记忆（Memory Chat V0）───
let _chatSending = false;

function bindCommentRefInteractions(rootEl) {
  if (!rootEl) return;
  activateCommentRefs(rootEl);
  rootEl.querySelectorAll(".kb-nb-cref").forEach(a => {
    a.addEventListener("mouseenter", () => showCrefHoverFor(a));
    a.addEventListener("mouseleave", () => hideCrefHover());
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      const id = parseInt(a.dataset.cid, 10);
      const c = await fetchComment(id);
      if (c && c.page_url) {
        window.open(c.page_url, "_blank", "noopener");
      } else {
        toast("c#" + id + " 没找到 / 没有来源页");
      }
    });
  });
}

async function loadChat() {
  const thread = $("kb-nb-chat-thread");
  try {
    const data = await api("/notebook/chat?limit=40");
    $("kb-nb-count-chat").textContent = data.items.length || 0;
    if (!data.items.length) {
      thread.innerHTML = `<div class="kb-nb-empty">还没有记忆问答。</div>`;
      return;
    }
    thread.innerHTML = data.items.map(renderChatMessage).join("");
    bindCommentRefInteractions(thread);
    thread.scrollTop = thread.scrollHeight;
  } catch (e) {
    thread.innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
}

function renderChatMessage(m) {
  const role = m.role === "user" ? "你" : "Margin";
  const body = m.role === "assistant" ? md(m.content || "") : escapeHtml(m.content || "").replace(/\n/g, "<br>");
  return `
    <div class="kb-nb-chat-msg ${m.role === "assistant" ? "assistant" : "user"}">
      <div class="kb-nb-chat-meta">${role} · ${fmtTimeAgo(m.created_at)}</div>
      <div class="kb-nb-chat-body">${body}</div>
    </div>
  `;
}

async function sendMemoryChat(message) {
  if (_chatSending) return;
  _chatSending = true;
  const btn = $("kb-nb-chat-submit");
  const input = $("kb-nb-chat-input");
  btn.disabled = true;
  btn.textContent = "读取中…";
  try {
    await api("/notebook/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message})
    });
    input.value = "";
    await loadChat();
  } catch (e) {
    toast("记忆问答失败");
  } finally {
    _chatSending = false;
    btn.disabled = false;
    btn.textContent = "发送";
  }
}

$("kb-nb-chat-form").addEventListener("submit", e => {
  e.preventDefault();
  const input = $("kb-nb-chat-input");
  const message = (input.value || "").trim();
  if (!message) return;
  sendMemoryChat(message);
});

// ─── 6. 共同日记 ───
async function loadDiary() {
  try {
    const data = await api("/notebook/diary?limit=80");
    const list = $("kb-nb-diary-list");
    if (!data.items.length) {
      list.innerHTML = `<div class="kb-nb-empty">还没有批注 · 去网页上划线评注一条试试</div>`;
      return;
    }
    const allAgentReplyIds = data.items.flatMap(c =>
      (c.replies || []).filter(r => r.author === "agent" && r.id !== undefined && r.id !== null).map(r => String(r.id))
    );
    const seenReplyIds = ensureAgentReplySeenBaseline(allAgentReplyIds);
    _diaryUnreadReplyIds = [];
    list.innerHTML = data.items.map(c => {
      const t = new Date(c.created_at);
      const ts = `${(t.getMonth()+1)}-${String(t.getDate()).padStart(2,"0")} ${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`;
      const excerpt = (c.selected_text || "").trim();
      const excerptHtml = excerpt ? `<div class="kb-nb-diary-quote">"${escapeHtml(excerpt.slice(0, 120))}${excerpt.length > 120 ? "…" : ""}"</div>` : "";
      const pageHtml = c.page_url ? `
        <div class="kb-nb-diary-page">
          <a href="${escapeHtml(c.page_url)}" target="_blank" rel="noopener">${escapeHtml(c.page_title || c.page_url)}</a>
        </div>` : "";
      // 把这条评论的 Margin replies 渲染成紧跟其后的 agent 流
      const aiReplies = (c.replies || []).filter(r => r.author === "agent");
      const repliesHtml = aiReplies.map(r => {
        const rt = new Date(r.created_at);
        const rts = `${(rt.getMonth()+1)}-${String(rt.getDate()).padStart(2,"0")} ${String(rt.getHours()).padStart(2,"0")}:${String(rt.getMinutes()).padStart(2,"0")}`;
        const replyId = String(r.id);
        const unread = !seenReplyIds.has(replyId);
        if (unread) _diaryUnreadReplyIds.push(replyId);
        const raw = r.content || "";
        const compact = raw.replace(/\s+/g, " ").trim();
        const preview = compact.slice(0, 220);
        const isLong = compact.length > 220 || raw.length > 320;
        return `
          <div class="kb-nb-diary-item agent ${unread ? "unread" : ""}" data-agent-reply-id="${escapeHtml(replyId)}">
            <div class="kb-nb-diary-meta">
              Margin · ${rts}
              ${unread ? `<span class="kb-nb-diary-unread-tag">未读</span>` : ""}
            </div>
            <div class="kb-nb-diary-text kb-nb-diary-preview">${escapeHtml(preview)}${isLong ? "…" : ""}</div>
            <div class="kb-nb-diary-text kb-nb-diary-full">${md(raw)}</div>
            <div class="kb-nb-diary-actions">
              <button data-read-reply="${escapeHtml(replyId)}">${isLong ? "展开阅读" : "标为已读"}</button>
            </div>
          </div>
        `;
      }).join("");
      return `
        <div class="kb-nb-diary-item">
          <div class="kb-nb-diary-meta">你 · ${ts}</div>
          <div class="kb-nb-diary-text">${escapeHtml(c.comment || "")}</div>
          ${excerptHtml}
          ${pageHtml}
        </div>
        ${repliesHtml}
      `;
    }).join("");
    list.innerHTML = `
      <div class="kb-nb-diary-unread-notice" id="kb-nb-diary-unread-notice"></div>
      ${list.innerHTML}
    `;
    updateDiaryUnreadNotice();
  } catch (e) {
    $("kb-nb-diary-list").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
}

$("kb-nb-diary-list").addEventListener("click", e => {
  const jumpBtn = e.target.closest("[data-jump-unread-reply]");
  if (jumpBtn) {
    scrollToFirstUnreadReply();
    return;
  }

  const markAllBtn = e.target.closest("[data-mark-all-ai-read]");
  if (markAllBtn) {
    markAgentRepliesSeen(_diaryUnreadReplyIds);
    toast("已标记全部 Margin 回复为已读");
    return;
  }

  const readBtn = e.target.closest("[data-read-reply]");
  if (readBtn) {
    const id = readBtn.dataset.readReply;
    const item = document.querySelector(`[data-agent-reply-id="${CSS.escape(String(id))}"]`);
    if (item) item.classList.add("expanded");
    markAgentRepliesSeen(id);
    return;
  }

  const item = e.target.closest("[data-agent-reply-id]");
  if (item) {
    item.classList.add("expanded");
    markAgentRepliesSeen(item.dataset.agentReplyId);
  }
});

// ─── 6. 导出（占位）───
document.querySelectorAll(".kb-nb-page-export, #kb-nb-export-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    toast("导出 Context Pack 开发中 · 见 design.md §7.2");
  });
});

// ─── 启动 ───
function init() {
  // 默认 tab
  const initial = (location.hash || "#profile-project").replace("#", "");
  const validTabs = ["chat", "profile-project", "thought-map", "rules", "thinking", "diary"];
  switchTab(validTabs.includes(initial) ? initial : "profile-project");

  loadOverview();
  loadProfile();
  loadRules();
  // thinking / diary 在切换 tab 时懒加载，但首次至少触发一次 thinking 计数
  api("/notebook/thinking").then(d => {
    $("kb-nb-count-thinking").textContent = d.archived_count + (d.active ? 1 : 0);
  }).catch(() => {});
  api("/notebook/chat?limit=1").then(d => {
    $("kb-nb-count-chat").textContent = d.items.length ? "…" : 0;
  }).catch(() => {});
  api("/notebook/thought-map").then(d => {
    $("kb-nb-count-thought-map").textContent = d.stats?.node_count || 0;
  }).catch(() => {});
  loadDiary();
}

init();
