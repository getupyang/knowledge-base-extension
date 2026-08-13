// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）
//
// 六状态状态机（2026-08-09 设计定稿 + 同日验收反馈修订：砍掉暂停 G 态）：
// A 未连接·初始   → 只做一个决策：用哪个 AI（Claude Code / Codex 主，API Key 折叠次选）
// B 等待连接      → 选完换屏；给一段话粘给正开着的 agent；后台轮询，检测到就绪自动进 E
// C API 表单      → 次选路径；Key 无效红字不跳走（真实调用 /config/ai/test 验证）
// D 连接遇到问题  → B 的故障版：一句人话说清问题 + 嵌入诊断结果的修复 prompt，
//                   用户只负责复制转发给 AI；诊断是喂给 agent 读的，不是给用户读的
// E 已连接 🟢     → 切换是明面按钮；数据备份（划线批注导出 + Notion）合并一个区块
// F 切换 AI       → 引擎在跑，POST /config/ai 内部即时切，不重装；目标没就绪 → D
//
// 三铁律：引擎对用户隐形（UI 不出现引擎/后端/8766/provider 字样，给 agent 的 prompt 除外）；
// AI 连接与 Notion 独立；失败时主动作是"帮你连上"，不是推 API Key。
// 硬承诺：粘给 agent → 跑完 → popup 不刷新自动变绿灯（每 2.5s 轮询 /health + /config）。
// 首次变绿灯时用一行提示介绍记忆笔记本（它此刻才出现，需要被介绍，不能突然冒出来）。

let API_BASE = "http://localhost:8766";
const API_BASE_OVERRIDE_KEY = "kb_api_base_override"; // 仅测试用：隔离后端实例

const CONN_KEY = "margin_conn_v1";       // { chosen, phase: idle|waiting|connected, waitingSince }
const AI_PAUSED_KEY = "kb_ai_paused_v1"; // 已废弃（暂停功能已砍），启动时清理历史标记
const VAULT_KEY = "kb_vault_v1";
const PAIR_TOKEN_KEY = "margin_pair_token"; // 认证配对协议（清单 1.3）：3.3 后端落地前向前兼容

const PROVIDER_LABEL = { claude_code: "Claude Code", codex_cli: "Codex", api: "API 服务" };
const SLOW_HINT_MS = 90 * 1000;        // B 态等待超过这个时长，出"看看哪里不对"入口
const WAIT_GIVEUP_MS = 10 * 60 * 1000; // 重开 popup 时等待已超过这个时长，直接进 D

let cur = null;            // 当前屏：A-F
let conn = { chosen: null, phase: "idle", waitingSince: 0 };
let runtimeState = null;   // GET /config 最近一次成功响应
let lastHealth = null;     // GET /health 最近一次成功响应
let pairToken = null;
let _pollTimer = null;
let _healthFailStreak = 0;

function $(id) {
  return document.getElementById(id);
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status ${kind}`.trim();
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text || "";
}

// ── 给 agent 的两段话（版本兼容协议 1.4 定稿措辞；PIN 来自 release-pin.js） ──

const INSTALL_SH_URL = `https://raw.githubusercontent.com/getupyang/knowledge-base-extension/${MARGIN_RELEASE_PIN}/install.sh`;

function installPrompt(provider) {
  const label = PROVIDER_LABEL[provider] || "Claude Code";
  return `请在我本机装好 Margin 的本地引擎：安装脚本在 ${INSTALL_SH_URL} （官方版本化地址，请从这里装，不要用其他来源）。用我的 ${label} 作为它的 AI。注意：不要覆盖我已有的批注数据（~/.knowledge-base-extension）。装好后启动服务、跑一次健康检查（curl http://localhost:8766/health），告诉我是否连上。需要系统权限或修改已有配置前先跟我说。`;
}

// 升级场景专用（2026-08-13 用户反馈修订）：引擎已在用、AI 已配好——
// 不指定用哪个 AI（收到这段话的 agent 就是用户的 AI），只强调保数据、保配置。
function upgradePrompt() {
  return `请帮我把本机已安装的 Margin 引擎升级到新版：升级脚本在 ${INSTALL_SH_URL} （官方版本化地址，请从这里升级，不要用其他来源）。我的批注数据（~/.knowledge-base-extension）和已有配置（~/.kb_config，包括我正在用的 AI 设置）都要原样保留，不要覆盖。升级完成后重启服务，跑一次健康检查（curl http://localhost:8766/health），确认返回里的版本号变新了，然后告诉我结果。过程中需要系统权限、或者要改动我已有的配置时，先停下来问我。`;
}

function repairPrompt(provider, diagText) {
  const label = PROVIDER_LABEL[provider] || "Claude Code";
  const diag = diagText ? `我这边看到的情况：${diagText}。` : "";
  return `我本机的 Margin 引擎没连上（可能服务没起、或没找到 ${label}）。${diag}请排查：curl http://localhost:8766/health 看服务是否在跑；看 ~/.knowledge-base-extension/.logs/failures.jsonl 里最近的失败；必要时重跑 ~/.knowledge-base-extension/app 里的 start.sh。修好后确认已用 ${label} 连上，告诉我结果。不要覆盖已有批注数据，改配置前先跟我说。`;
}

// ── 持久化：连接进度 / 暂停标记 ──

async function readConn() {
  try {
    const stored = await chrome.storage.local.get(CONN_KEY);
    if (stored[CONN_KEY]) conn = { ...conn, ...stored[CONN_KEY] };
  } catch {}
}

async function writeConn(patch) {
  conn = { ...conn, ...patch };
  try { await chrome.storage.local.set({ [CONN_KEY]: conn }); } catch {}
}

// 暂停功能已砍（2026-08-09 验收反馈：不需要让用户暂停 AI）。
// 旧 key 启动时清理，防止历史标记让 content script 静默压掉召唤 AI 入口。
async function clearLegacyAiPaused() {
  try { await chrome.storage.local.remove(AI_PAUSED_KEY); } catch {}
}

// ── 后端调用（带配对 token；见 1.3 协议） ──

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (pairToken) headers["X-Margin-Token"] = pairToken;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function tryPair() {
  if (pairToken) return;
  try {
    const data = await api("/pair", { method: "POST" });
    if (data && data.token) {
      pairToken = data.token;
      await chrome.storage.local.set({ [PAIR_TOKEN_KEY]: pairToken });
    }
  } catch {
    // 3.3 落地前后端没有 /pair（404）：静默降级，不阻塞连接流
  }
}

// ── 本机批注库（vault）：统计与导出，不依赖后端在线 ──

async function readVault() {
  const stored = await chrome.storage.local.get(VAULT_KEY);
  return stored[VAULT_KEY];
}

async function loadVaultStats() {
  try {
    const s = KBVaultCore.stats(await readVault());
    if (s.pages) {
      setText("vaultStatus", `${s.comments} 条批注 · ${s.highlights} 条高亮`);
      setText("vaultDetail", `来自 ${s.pages} 个页面，随时可导出带走`);
    } else {
      setText("vaultStatus", "还没有内容");
      setText("vaultDetail", "在网页上划线批注后，这里可以导出备份");
    }
    $("exportMdBtn").disabled = !s.pages;
    $("exportJsonBtn").disabled = !s.pages;
  } catch {
    setText("vaultStatus", "无法读取");
    setText("vaultDetail", "");
  }
}

function downloadTextFile(filename, mime, text) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function exportVault(format) {
  try {
    const vault = await readVault();
    const now = new Date();
    const stamp = now.toISOString().slice(0, 10);
    if (format === "md") {
      downloadTextFile(`margin-export-${stamp}.md`, "text/markdown", KBVaultCore.toMarkdown(vault, now.toISOString()));
    } else {
      const obj = KBVaultCore.toExportObject(vault, now.toISOString());
      downloadTextFile(`margin-export-${stamp}.json`, "application/json", JSON.stringify(obj, null, 2));
    }
    setStatus("已导出到下载文件夹", "");
  } catch (err) {
    setStatus(err.message || "导出失败", "error");
  }
}

// ── 屏幕切换 ──

const SCREEN_IDS = ["A", "B", "C", "D", "E", "F"];

function go(id, opt = {}) {
  if (opt.chosen) writeConn({ chosen: opt.chosen });
  if (id === "B") setupB(opt);
  if (id === "C") setupC();
  if (id === "D") setupD();
  if (id === "E") setupE();
  if (id === "F") setupF();

  for (const s of SCREEN_IDS) {
    const el = $(`screen${s}`);
    if (s === id) {
      el.hidden = false;
      el.classList.add("enter");
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("enter")));
    } else {
      el.hidden = true;
    }
  }
  cur = id;
  document.body.dataset.state = id;
  // 记忆笔记本常驻可见：未连接置灰（点不了），连上点亮；灰→亮的过渡本身就是介绍（2026-08-11）
  $("notebookBtn").disabled = id !== "E";
  $("notebookHint").hidden = id === "E";
  setStatus("");
}

// ── B 等待连接 ──

function setupB(opt = {}) {
  const provider = opt.chosen || conn.chosen || "claude_code";
  const label = PROVIDER_LABEL[provider] || "Claude Code";
  document.querySelectorAll(".bProv").forEach((el) => { el.textContent = label; });
  $("bPrompt").textContent = installPrompt(provider);
  const copied = conn.phase === "waiting";
  $("bWait").hidden = !copied;
  $("bCopyBtn").textContent = copied ? "再复制一次" : "复制这段话";
  $("bSlow").hidden = !(copied && Date.now() - (conn.waitingSince || 0) > SLOW_HINT_MS);
}

async function copyBPrompt() {
  try {
    await navigator.clipboard.writeText($("bPrompt").textContent);
  } catch {
    setStatus("复制失败，请手动选中这段话复制", "error");
    return;
  }
  await writeConn({ phase: "waiting", waitingSince: conn.waitingSince || Date.now() });
  $("bWait").hidden = false;
  $("bCopyBtn").textContent = "再复制一次";
}

// ── C API 表单 ──

function defaultApiModel(provider) {
  return provider === "openrouter" ? "openai/gpt-4o-mini" : "qwen3.5-plus";
}

function setupC() {
  const ai = (runtimeState || {}).ai || {};
  $("cQwenWrap").hidden = $("cProvider").value !== "qwen";
  $("cKey").placeholder = ai.apiKeySet ? "已保存；留空表示继续使用" : "粘贴 API Key";
  $("cErr").hidden = true;
}

function friendlyKeyError(raw) {
  const msg = String(raw || "");
  if (/401|invalid|unauthorized|api key/i.test(msg)) return "这个 Key 验证没通过，检查一下再试。";
  if (/404|model/i.test(msg)) return "模型名可能不对，检查一下再试。";
  if (/timeout|url error/i.test(msg)) return "连不上这个服务商，检查网络后再试。";
  return `验证没通过：${msg.slice(0, 120)}`;
}

async function saveCConfig() {
  const ai = (runtimeState || {}).ai || {};
  const apiKey = $("cKey").value.trim();
  if (!apiKey && !ai.apiKeySet) {
    $("cErr").textContent = "先粘贴一个 API Key。";
    $("cErr").hidden = false;
    return;
  }
  const payload = {
    provider: "api",
    apiProvider: $("cProvider").value,
    apiKey,
    model: $("cModel").value.trim() || defaultApiModel($("cProvider").value),
    qwenEndpoint: $("cQwenEndpoint").value,
  };
  const btn = $("cSaveBtn");
  btn.disabled = true;
  btn.textContent = "正在验证 Key…";
  $("cErr").hidden = true;
  try {
    // 真实调用一次验证 Key（旧后端没有 /config/ai/test → 404 时跳过验证直接保存）
    try {
      const test = await api("/config/ai/test", { method: "POST", body: JSON.stringify(payload) });
      if (test && test.ok === false) {
        $("cErr").textContent = friendlyKeyError(test.error);
        $("cErr").hidden = false;
        return;
      }
    } catch (err) {
      if (err.status !== 404 && err.status !== 405) throw err;
    }
    btn.textContent = "正在连接…";
    await api("/config/ai", { method: "POST", body: JSON.stringify(payload) });
    runtimeState = await api("/config");
    $("cKey").value = "";
    if (runtimeState.ai && runtimeState.ai.configured) {
      await writeConn({ chosen: "api", phase: "connected" });
      go("E");
    } else {
      $("cErr").textContent = friendlyKeyError((runtimeState.ai || {}).error);
      $("cErr").hidden = false;
    }
  } catch (err) {
    $("cErr").textContent = friendlyKeyError(err.message);
    $("cErr").hidden = false;
  } finally {
    btn.disabled = false;
    if (btn.textContent !== "保存，连接") btn.textContent = "保存，连接";
  }
}

// ── D 连接遇到问题：B 的故障版 ──
// 交互模式与 B 完全一致（问题一句话 → 写好的话 → 复制转发 → 自动等绿灯）。
// 诊断结果不给用户读，直接嵌进 prompt 给 agent 读。

function setupD() {
  setText("dProblem", "正在看是哪里的问题…");
  $("dPrompt").textContent = repairPrompt(conn.chosen, "");
  $("dWait").hidden = true;
  $("dCopyBtn").textContent = "复制这段话";
  // 还在等 agent 装的人要有回头路（回 B 继续等）；连过后来断了的故障场景不显示（修复话术才是正路）
  $("dBack").hidden = conn.phase !== "waiting";
  refreshDProblem();
}

async function refreshDProblem() {
  const label = PROVIDER_LABEL[conn.chosen] || "AI";
  let problem = "";
  let diag = "";
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error("health not ok");
    let detail = "";
    try {
      const cfg = await api("/config");
      const ai = cfg.ai || {};
      if (ai.configured) return; // 其实已连上：轮询下一拍会带去 E，这里不用管
      detail = ai.error ? String(ai.error).slice(0, 140) : "";
    } catch (e) {
      detail = e.message;
    }
    let failures = "";
    try {
      const f = await api("/failures?limit=3");
      if (f.failures && f.failures.length) {
        failures = f.failures
          .map((i) => `${(i.ts || "").slice(0, 16)} ${i.phase || ""} ${i.error_type || ""}`.trim())
          .join("；");
      }
    } catch {}
    problem = `本地准备好了，但还没找到你的 ${label}`;
    diag = `本地服务(localhost:8766)在跑，但 AI 没接上${detail ? `，报错：${detail}` : ""}${failures ? `。最近失败记录：${failures}` : ""}`;
  } catch {
    problem = "本地还没准备好（可能没装完，或没在运行）";
    diag = "curl http://localhost:8766/health 没有响应";
  }
  if (cur !== "D") return; // 用户已离开 D 屏，别写穿
  setText("dProblem", `问题：${problem}。不是你的错——把下面这段话发给你的 ${label}，它会帮你修好。`);
  $("dPrompt").textContent = repairPrompt(conn.chosen, diag);
}

async function copyDPrompt() {
  try {
    await navigator.clipboard.writeText($("dPrompt").textContent);
  } catch {
    setStatus("复制失败，请手动选中这段话复制", "error");
    return;
  }
  $("dWait").hidden = false;
  $("dCopyBtn").textContent = "再复制一次";
}

// ── E 已连接 ──

const PROVIDER_SHORT = { codex_cli: "Codex", claude_code: "Claude Code" };

function shortAiName(ai) {
  if (ai.selectedProvider === "api") return ai.displayName || "API 服务";
  return PROVIDER_SHORT[ai.selectedProvider] || ai.displayName || "AI 服务";
}

function setupE() {
  const ai = (runtimeState || {}).ai || {};
  setText("eProv", shortAiName(ai));
  const schema = (lastHealth && lastHealth.api_schema) || 0;
  $("eUpgrade").hidden = schema === MARGIN_EXPECTED_API_SCHEMA;
  if (runtimeState) renderNotion(runtimeState);
  loadVaultStats();
}

// ── F 切换 AI ──

function setupF() {
  const ai = (runtimeState || {}).ai || {};
  const available = ai.available || {};
  const current = ai.selectedProvider || "";
  const rows = [
    ["fClaude", "claude_code", "使用你的 Claude 订阅"],
    ["fCodex", "codex_cli", "使用你的 ChatGPT 订阅"],
    ["fApi", "api", "千问 / OpenRouter"],
  ];
  for (const [btnId, provider, baseNote] of rows) {
    const btn = $(btnId);
    btn.classList.toggle("is-selected", current === provider);
    let note = baseNote;
    if (current === provider) note = "当前正在使用";
    else if (provider !== "api" && !available[provider]) note = "这台电脑上还没检测到";
    else if (provider === "api" && !ai.apiKeySet) note = "千问 / OpenRouter，需要填一个 API Key";
    btn.querySelector("small").textContent = note;
  }
}

async function switchProvider(provider) {
  const ai = (runtimeState || {}).ai || {};
  if (ai.selectedProvider === provider) {
    go("E");
    return;
  }
  if (provider === "api" && !ai.apiKeySet) {
    go("C", { chosen: "api" });
    return;
  }
  setStatus(`正在切换到 ${PROVIDER_LABEL[provider]}…`, "neutral");
  try {
    const payload = { provider };
    if (provider === "api") {
      payload.apiProvider = ai.apiProvider || "qwen";
      payload.model = ai.apiModel || "";
    }
    await api("/config/ai", { method: "POST", body: JSON.stringify(payload) });
    runtimeState = await api("/config");
    if (runtimeState.ai && runtimeState.ai.configured) {
      await writeConn({ chosen: provider, phase: "connected" });
      go("E");
      setStatus(`已切换到 ${shortAiName(runtimeState.ai)}`, "");
    } else {
      await writeConn({ chosen: provider });
      go("D");
    }
  } catch (err) {
    // 目标没就绪（如本机没装该 CLI）→ D，不推 API Key
    await writeConn({ chosen: provider });
    go("D");
    setStatus(err.message || "", "neutral");
  }
}

// ── Notion（真正可选的功能，保留开关语义；与 AI 连接独立） ──

function renderNotion(data) {
  const notion = data.notion || {};
  const notionSwitch = $("notionSwitch");
  notionSwitch.disabled = false;
  const notionOn = Boolean(notion.configured || data.notionConfigured);
  notionSwitch.checked = notionOn;
  if (notionOn) {
    setText("backupStatus", "已开启");
    setText("backupDetail", "新批注会额外备份一份到你的 Notion");
  } else if (notion.saved) {
    setText("backupStatus", "已暂停");
    setText("backupDetail", "配置已保留，打开开关即可恢复");
  } else {
    setText("backupStatus", "未开启");
    setText("backupDetail", "打开后额外备份一份到你的 Notion");
  }
  syncNotionForm(notion);
}

function toggleNotionPanel(forceOpen) {
  const panel = $("notionPanel");
  panel.hidden = forceOpen === true ? false : !panel.hidden;
}

async function onNotionSwitchChange() {
  const sw = $("notionSwitch");
  const notion = (runtimeState || {}).notion || {};
  if (sw.checked && !notion.saved && !notion.configured) {
    // 首次开启需要 Token：弹回，打开配置面板
    sw.checked = false;
    toggleNotionPanel(true);
    return;
  }
  const wantOn = sw.checked;
  try {
    const data = await api("/config/notion", {
      method: "POST",
      body: JSON.stringify({ enabled: wantOn }),
    });
    runtimeState = { ...(runtimeState || {}), notion: data.notion, notionConfigured: !!data.notion?.configured };
    renderNotion(runtimeState);
    setStatus(wantOn ? "Notion 备份已开启" : "Notion 备份已暂停", wantOn ? "" : "neutral");
  } catch (err) {
    sw.checked = !wantOn;
    if (wantOn) toggleNotionPanel(true);
    setStatus(err.message || "操作失败", "error");
  }
}

async function saveNotionConfig() {
  const token = $("notionToken").value.trim();
  const databaseId = $("notionDatabaseId").value.trim();
  setStatus("正在保存 Notion 配置...", "neutral");
  try {
    const data = await api("/config/notion", {
      method: "POST",
      body: JSON.stringify({ token, databaseId, enabled: true }),
    });
    runtimeState = { ...(runtimeState || {}), notion: data.notion, notionConfigured: !!data.notion?.configured };
    renderNotion(runtimeState);
    $("notionToken").value = "";
    $("notionPanel").hidden = true;
    setStatus("Notion 已开启", "");
  } catch (err) {
    setStatus(err.message || "Notion 配置失败", "error");
  }
}

async function disableNotionConfig() {
  setStatus("正在暂停 Notion 备份...", "neutral");
  try {
    const data = await api("/config/notion", {
      method: "POST",
      body: JSON.stringify({ enabled: false }),
    });
    runtimeState = { ...(runtimeState || {}), notion: data.notion, notionConfigured: false };
    renderNotion(runtimeState);
    $("notionPanel").hidden = true;
    setStatus("Notion 备份已暂停", "");
  } catch (err) {
    setStatus(err.message || "关闭失败", "error");
  }
}

async function createNotionDatabase() {
  const token = $("notionToken").value.trim();
  const parentPage = $("notionParentPage").value.trim();
  setStatus("正在创建 Notion 数据库...", "neutral");
  $("createNotionDatabaseBtn").disabled = true;
  try {
    const data = await api("/config/notion/create-database", {
      method: "POST",
      body: JSON.stringify({ token, parentPage }),
    });
    runtimeState = { ...(runtimeState || {}), notion: data.notion, notionConfigured: !!data.notion?.configured };
    renderNotion(runtimeState);
    $("notionDatabaseId").value = data.databaseId || "";
    $("notionToken").value = "";
    $("notionParentPage").value = "";
    $("notionPanel").hidden = true;
    setStatus("Notion 数据库已创建", "");
  } catch (err) {
    setStatus(err.message || "创建失败", "error");
  } finally {
    $("createNotionDatabaseBtn").disabled = false;
  }
}

function syncNotionForm(notion) {
  $("notionToken").placeholder = notion.tokenSet ? "已保存；留空表示继续使用" : "ntn_...";
  if (notion.databaseId && !$("notionDatabaseId").value.trim()) {
    $("notionDatabaseId").value = notion.databaseId;
  }
  $("saveNotionBtn").textContent = notion.saved && !notion.enabled ? "重新开启云端备份" : "保存并开启";
  $("disableNotionBtn").disabled = !notion.configured;
  $("notionHint").innerHTML = notion.saved
    ? "已保存过 Notion 配置。暂停云端备份不会删除 Token 和 Database ID。"
    : "<strong>第一次配置：</strong>先打开 Notion integrations 创建 integration 并复制 Secret；再新建一个空白 Notion 页面，在 Share / Connections 里授权这个 integration，把这个页面链接粘贴到上面，点自动创建数据库。";
}

// ── 连接轮询：硬承诺"粘给 agent → 跑完 → 不刷新自动变绿灯" ──

async function pollTick() {
  let healthOk = false;
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (res.ok) {
      healthOk = true;
      try { lastHealth = await res.json(); } catch { lastHealth = null; }
    }
  } catch {}

  if (!healthOk) {
    _healthFailStreak += 1;
    // E 态下引擎失联（连续两次，防抖动）→ D
    if (cur === "E" && _healthFailStreak >= 2) go("D");
    // B 态等待太久 → 给出口（不自动打断，用户可能还在等 agent 装）
    if (cur === "B" && conn.phase === "waiting" && Date.now() - (conn.waitingSince || 0) > SLOW_HINT_MS) {
      $("bSlow").hidden = false;
    }
    return;
  }
  _healthFailStreak = 0;

  let cfg = null;
  try {
    cfg = await api("/config");
  } catch {
    return;
  }
  runtimeState = cfg;
  const configured = Boolean(cfg.ai && cfg.ai.configured);

  // C（正在填表单）/ F（正在选）不被自动跳转打断
  if (configured && (cur === "A" || cur === "B" || cur === "D")) {
    await writeConn({ phase: "connected" });
    go("E");
    return;
  }
  if (cur === "B" && conn.phase === "waiting" && Date.now() - (conn.waitingSince || 0) > SLOW_HINT_MS) {
    $("bSlow").hidden = false;
  }
}

function startConnPolling() {
  if (_pollTimer) return;
  _pollTimer = setInterval(pollTick, 2500);
}

// ── 初始状态决策 ──

async function decideInitialState() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error("health not ok");
    try { lastHealth = await res.json(); } catch {}
    runtimeState = await api("/config");
    if (runtimeState.ai && runtimeState.ai.configured) {
      await writeConn({ phase: "connected" });
      go("E");
      return;
    }
    // 引擎在跑但 AI 没接上：等待中回 B 继续等，否则回选择屏
    if (conn.phase === "waiting") go("B");
    else go("A");
  } catch {
    // 引擎不在：按此前进度决定
    if (conn.phase === "waiting") {
      if (Date.now() - (conn.waitingSince || 0) > WAIT_GIVEUP_MS) go("D");
      else go("B");
    } else if (conn.phase === "connected") {
      go("D"); // 连过但现在失联 = 故障，不是新用户
    } else {
      go("A");
    }
  }
}

// ── 启动 ──

document.addEventListener("DOMContentLoaded", async () => {
  // 事件绑定
  $("notebookBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("src/notebook/index.html") });
    window.close();
  });

  // A：选 AI
  $("aChooseClaude").addEventListener("click", () => go("B", { chosen: "claude_code" }));
  $("aChooseCodex").addEventListener("click", () => go("B", { chosen: "codex_cli" }));
  $("aChooseApi").addEventListener("click", () => go("C", { chosen: "api" }));

  // B：复制 + 返回 + 慢速出口
  $("bBack").addEventListener("click", async () => {
    await writeConn({ phase: "idle", waitingSince: 0 });
    go("A");
  });
  $("bCopyBtn").addEventListener("click", copyBPrompt);
  $("bSlowLink").addEventListener("click", () => go("D"));

  // C：表单
  $("cBack").addEventListener("click", () => go("A"));
  $("cProvider").addEventListener("change", () => {
    $("cQwenWrap").hidden = $("cProvider").value !== "qwen";
    $("cModel").value = defaultApiModel($("cProvider").value);
    $("cErr").hidden = true;
  });
  $("cSaveBtn").addEventListener("click", saveCConfig);

  // D：返回继续等 / 复制修复 prompt / 换 AI
  $("dBack").addEventListener("click", () => go("B"));
  $("dCopyBtn").addEventListener("click", copyDPrompt);
  $("dSwitchBtn").addEventListener("click", async () => {
    await writeConn({ phase: "idle", waitingSince: 0 });
    go("A");
  });

  // E：切换 / 升级指令
  $("eSwitchBtn").addEventListener("click", () => go("F"));
  $("eUpgradeCopy").addEventListener("click", async () => {
    try {
      // 修订（2026-08-13）：升级场景用专用指令，不再复用首装指令——
      // 首装指令会点名 AI（且 conn.chosen 为空时误报 Claude Code），升级无需换 AI
      await navigator.clipboard.writeText(upgradePrompt());
      setStatus("已复制，粘给你的 AI 让它升级引擎", "");
    } catch {
      setStatus("复制失败", "error");
    }
  });

  // F：切换目标
  $("fBack").addEventListener("click", () => go("E"));
  $("fClaude").addEventListener("click", () => switchProvider("claude_code"));
  $("fCodex").addEventListener("click", () => switchProvider("codex_cli"));
  $("fApi").addEventListener("click", () => switchProvider("api"));

  // 导出
  $("exportConfigBtn").addEventListener("click", () => {
    $("exportPanel").hidden = !$("exportPanel").hidden;
  });
  $("closeExportPanelBtn").addEventListener("click", () => {
    $("exportPanel").hidden = true;
    setStatus("");
  });
  $("exportMdBtn").addEventListener("click", () => exportVault("md"));
  $("exportJsonBtn").addEventListener("click", () => exportVault("json"));

  // Notion
  $("notionSwitch").addEventListener("change", onNotionSwitchChange);
  $("notionRowInfo").addEventListener("click", () => { if (runtimeState) toggleNotionPanel(); });
  $("saveNotionBtn").addEventListener("click", saveNotionConfig);
  $("disableNotionBtn").addEventListener("click", disableNotionConfig);
  $("createNotionDatabaseBtn").addEventListener("click", createNotionDatabase);
  $("openNotionIntegrationsBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://www.notion.so/my-integrations" });
  });
  $("closeNotionPanelBtn").addEventListener("click", () => {
    $("notionPanel").hidden = true;
    setStatus("");
  });

  // 初始化：测试环境 API base 覆盖 → 配对 token → 连接进度 → 决定初始屏 → 起轮询
  try {
    const stored = await chrome.storage.local.get([API_BASE_OVERRIDE_KEY, PAIR_TOKEN_KEY]);
    if (stored[API_BASE_OVERRIDE_KEY]) API_BASE = stored[API_BASE_OVERRIDE_KEY];
    if (stored[PAIR_TOKEN_KEY]) pairToken = stored[PAIR_TOKEN_KEY];
  } catch {}
  await clearLegacyAiPaused();
  await readConn();
  await tryPair();
  await decideInitialState();
  startConnPolling();
});
