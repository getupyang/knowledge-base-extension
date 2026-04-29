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
  if (tab === "thinking") loadThinking();
  if (tab === "diary") loadDiary();
}

document.querySelectorAll(".kb-nb-nav-item").forEach(a => {
  a.addEventListener("click", e => {
    e.preventDefault();
    switchTab(a.dataset.tab);
  });
});

// ─── 1. 顶部 overview + 底部 callout ───
async function loadOverview() {
  try {
    const data = await api("/notebook/overview");
    const sync = $("kb-nb-overview-sync");
    sync.textContent = `同步 ${fmtClock(data.latest_sync)} · ${data.comment_count} 条评注 · ${data.page_count} 篇文章`;
    $("kb-nb-overview-meta").textContent = `共 ${data.page_count} 篇 · 评注 ${data.comment_count} 条`;
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
  } catch (e) {
    $("kb-nb-overview-sync").textContent = "后端离线（localhost:8766）";
    $("kb-nb-callout-body").innerHTML = `<em>后端 agent_api.py 没启动？</em><br><span class="kb-nb-mono-soft">cd backend && python3 agent_api.py</span>`;
  }
}

// ─── 2. 你 & 项目 ───
async function loadProfile() {
  try {
    const data = await api("/notebook/profile");
    const profileEl = $("kb-nb-profile-md");
    const projectEl = $("kb-nb-project-md");
    if (data.user_profile_md && data.user_profile_md.trim()) {
      profileEl.innerHTML = md(data.user_profile_md);
    } else {
      profileEl.innerHTML = `<div class="kb-nb-empty">user_profile.md 还是空的。<br>多批注几篇文章，AI 会替你写一稿。</div>`;
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
}

// ─── 3. 养成的习惯（rules）───
async function loadRules() {
  try {
    const data = await api("/notebook/rules");
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

// 把渲染后的 HTML 里 [c#NNN] 文本节点替换为可点击锚点
function activateCommentRefs(rootEl) {
  const re = /\[c#(\d+)\]/g;
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
      const id = parseInt(m[1], 10);
      const a = document.createElement("a");
      a.className = "kb-nb-cref";
      a.dataset.cid = String(id);
      a.href = "#c" + id;
      a.textContent = "c#" + id;
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
  const id = parseInt(anchor.dataset.cid, 10);
  const el = ensureCrefHover();
  const rect = anchor.getBoundingClientRect();
  el.style.left = (rect.left + window.scrollX) + "px";
  el.style.top = (rect.bottom + window.scrollY + 6) + "px";
  el.innerHTML = `<div class="kb-nb-cref-loading">读取 c#${id}…</div>`;
  el.style.display = "block";
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
    box.innerHTML = `<div class="kb-nb-thinking-running">AI 正在用 Opus 整理你最近的思考，约 30–60 秒…</div>`;
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
        <li>Opus 限流（看 <code>backend/.logs/worker.log</code> 是否 429 / quota）</li>
        <li>JSON 解析失败（worker 已加多策略 fallback，但仍可能踩雷）</li>
        <li>claude CLI 超时</li>
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

// ─── 5. 共同日记 ───
async function loadDiary() {
  try {
    const data = await api("/notebook/diary?limit=80");
    const list = $("kb-nb-diary-list");
    if (!data.items.length) {
      list.innerHTML = `<div class="kb-nb-empty">还没有批注 · 去网页上划线评注一条试试</div>`;
      return;
    }
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
        const preview = (r.content || "").replace(/\s+/g, " ").trim().slice(0, 200);
        return `
          <div class="kb-nb-diary-item agent">
            <div class="kb-nb-diary-meta">AGENT · ${rts}</div>
            <div class="kb-nb-diary-text">${escapeHtml(preview)}${(r.content || "").length > 200 ? "…" : ""}</div>
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
  } catch (e) {
    $("kb-nb-diary-list").innerHTML = `<div class="kb-nb-empty">读取失败</div>`;
  }
}

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
  const validTabs = ["profile-project", "rules", "thinking", "diary"];
  switchTab(validTabs.includes(initial) ? initial : "profile-project");

  loadOverview();
  loadProfile();
  loadRules();
  // thinking / diary 在切换 tab 时懒加载，但首次至少触发一次 thinking 计数
  api("/notebook/thinking").then(d => {
    $("kb-nb-count-thinking").textContent = d.archived_count + (d.active ? 1 : 0);
  }).catch(() => {});
}

init();
