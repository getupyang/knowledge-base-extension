// mem-ai 笔记本 - 真实数据驱动，无 mock
// 后端：~/Documents/ai/coding/knowledge-base-extension/backend/agent_api.py
// 设计：~/mem-ai/docs/memory-backend-design.md

const API_BASE = "http://localhost:8766";

// ─── 工具 ───
function $(id) { return document.getElementById(id); }

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
      diaryCount.textContent = String(count);
      diaryCount.classList.add("unread");
      diaryCount.title = `${count} 条未读 AI 回复`;
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
        <div><strong>AI 回来了</strong><span>${count} 条回复待看</span></div>
        <button data-open-unread-replies>查看</button>
      `;
    }
  }

  if (!notice) return;
  if (!count) {
    notice.innerHTML = _diaryBaselineInitializedNow
      ? `<span>已建立已读基线，之后新完成的 AI 回复会在这里提醒</span>`
      : `<span>没有未读 AI 回复</span>`;
    notice.classList.add("empty");
    return;
  }
  notice.classList.remove("empty");
  notice.innerHTML = `
    <span>${count} 条未读 AI 回复</span>
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
  if (tab === "thinking") loadThinking();
  if (tab === "diary") loadDiary();
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
  toast("正在从 Notion 导入旧数据…");
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
      toast("Notion 没有可导入的旧数据");
    }
  } catch (e) {
    console.warn("[notebook] import notion failed", e);
    toast("Notion 旧数据导入失败");
  }
}

async function loadOverview() {
  try {
    const data = await api("/notebook/overview");
    const sync = $("kb-nb-overview-sync");
    sync.textContent = `同步 ${fmtClock(data.latest_sync)} · ${data.comment_count} 条评注 · ${data.page_count} 篇文章`;
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
    $("kb-nb-callout-body").innerHTML = `<em>后端 agent_api.py 没启动？</em><br><span class="kb-nb-mono-soft">cd backend && python3 agent_api.py</span>`;
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
        <div class="kb-nb-profile-field-label">当前 PROJECT</div>
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
  box.innerHTML = `<div class="kb-nb-curated-running">让 Opus 读你最近 30 条批注 + 资料，约 30 秒…</div>`;
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
      list.innerHTML = `<div class="kb-nb-empty">AI 还没从你的反馈里学到任何工作方式。<br>批注、纠正它的回答，它会开始记。</div>`;
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
  box.innerHTML = `
    <div class="kb-nb-skills-header">
      <h3 class="kb-nb-skills-title">我正在养成的 ${skillCount} 个工作方式</h3>
      <button class="kb-nb-profile-oneliner-refresh" id="kb-nb-rules-recurate">重新提炼 · 上次 ${ageStr}</button>
    </div>
    <p class="kb-nb-skills-subtitle">这是我作为你的协作者在长出的能力 — 来自你 ${data._total_rules || 0} 条具体反馈。</p>
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

  // 无 generation 或 DB 读失败 → 显示"让 AI 整理一稿"按钮
  const btn = document.getElementById("kb-nb-rules-curate-btn");
  if (btn) btn.onclick = () => triggerRulesCurated(false);
}

async function triggerRulesCurated(isRefresh) {
  const box = $("kb-nb-rules-curated");
  box.innerHTML = `<div class="kb-nb-curated-running">让 Opus 把零散规则提炼成 3-6 个工作方式，约 30 秒…</div>`;
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
    const ts = data._generation_id ? Date.now() : Date.now();
    renderRulesCurated(data, ts);
    toast(isRefresh ? "重新蒸馏完成" : "蒸馏完成");
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
        <span style="opacity:0.7">M3 启用 usage_count</span>
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

function renderThinking(data) {
  const box = $("kb-nb-thinking-active");
  // 优先展示 running 状态
  if (data.running_job) {
    box.innerHTML = `<div class="kb-nb-thinking-running">AI 正在整理你最近的思考，Codex 本地模式可能需要 1–3 分钟…</div>`;
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
  // 绑定 hover/click
  box.querySelectorAll(".kb-nb-cref").forEach(a => {
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

async function requestThinking(reason) {
  try {
    const r = await api("/jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ kind: "synthesize_thinking", payload: { trigger_reason: reason } })
    });
    toast(r.deduped ? "已经在跑了 · 等一下" : "已 queue · worker 正在跑");
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
  const role = m.role === "user" ? "你" : "AGENT";
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
      // 把这条评论的 AI replies 渲染成紧跟其后的 agent 流
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
              AGENT · ${rts}
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
    toast("已标记全部 AI 回复为已读");
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
  const validTabs = ["chat", "profile-project", "rules", "thinking", "diary"];
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
  loadDiary();
}

init();
