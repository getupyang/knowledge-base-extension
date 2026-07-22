// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）
//
// v4.1 状态机（2026-07-22 定稿）：AI 引擎是产品本体，没有 AI 开关。
// S0 引擎未装（新用户）    → 单向三步引导卡：装引擎 → 自动连接 → 绿灯
// SE 装过但没在跑          → 黄灯故障态，主按钮「启动」（install.sh 幂等兼修复）
// S1 在线但没连上任何服务   → 兜底：直接 API Key 表单（本机确认没有 Claude Code/Codex）
// S2 就绪                  → 绿灯常亮；「详情」进配置台（切换是一等操作，暂停是底部灰字）
//
// 首次自动连接优先级由后端决定：Claude Code → Codex → API Key，popup 只读结果。

const API_BASE = "http://localhost:8766";

let runtimeState = null;
// aiActionBtn 的当前语义：console（详情/配置台）| start（SE 修复）| resume（暂停恢复）
let aiActionMode = "console";
let aiDraft = {
  provider: "codex_cli",
  apiProvider: "qwen",
  model: "qwen3.5-plus",
  qwenEndpoint: "qwen_cn",
  claudeBaseUrl: "",
};

function $(id) {
  return document.getElementById(id);
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

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status ${kind}`.trim();
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text || "";
}

function setAiValue(dotClass, text) {
  const el = $("aiStatus");
  el.textContent = "";
  if (dotClass) {
    const d = document.createElement("span");
    d.className = `dot ${dotClass}`;
    el.appendChild(d);
  }
  el.appendChild(document.createTextNode(text));
}

function togglePanel(id) {
  for (const panelId of ["consolePanel", "startPanel", "notionPanel", "exportPanel"]) {
    const panel = $(panelId);
    panel.hidden = panelId === id ? !panel.hidden : true;
  }
}

// ── 本机批注库（vault）：统计与导出，不依赖后端在线 ──

const VAULT_KEY = "kb_vault_v1";

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

// ── 引擎连接 ──

const ENGINE_SEEN_KEY = "kb_engine_seen_v1";
const INSTALL_CMD = "curl -fsSL https://raw.githubusercontent.com/getupyang/knowledge-base-extension/main/install.sh | bash";
let _healthPollTimer = null;

async function engineSeenBefore() {
  try {
    const stored = await chrome.storage.local.get(ENGINE_SEEN_KEY);
    return !!stored[ENGINE_SEEN_KEY];
  } catch {
    return false;
  }
}

function markEngineSeen() {
  try { chrome.storage.local.set({ [ENGINE_SEEN_KEY]: true }); } catch {}
}

function startHealthPolling() {
  if (_healthPollTimer) return;
  _healthPollTimer = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        clearInterval(_healthPollTimer);
        _healthPollTimer = null;
        $("startPanel").hidden = true;
        loadRuntimeStatus();
      }
    } catch { /* 继续等 */ }
  }, 2500);
}

// AI 暂停标记（popup 写，content script 在召唤 AI 入口读）
const AI_PAUSED_KEY = "kb_ai_paused_v1";

async function readAiPaused() {
  try {
    const stored = await chrome.storage.local.get(AI_PAUSED_KEY);
    return !!stored[AI_PAUSED_KEY];
  } catch {
    return false;
  }
}

async function setAiPaused(paused) {
  try {
    if (paused) await chrome.storage.local.set({ [AI_PAUSED_KEY]: true });
    else await chrome.storage.local.remove(AI_PAUSED_KEY);
  } catch {}
}

// ── 状态机渲染 ──

function showState(state) {
  $("s0Hero").hidden = state !== "s0";
  $("s1Hero").hidden = state !== "s1";
  $("aiBlock").hidden = state !== "s2" && state !== "se";
}

async function renderOffline() {
  runtimeState = null;
  const notionSwitch = $("notionSwitch");
  notionSwitch.checked = false;
  notionSwitch.disabled = true;
  setText("backupStatus", "未开启");
  setText("backupDetail", "引擎上线后可用");

  const seen = await engineSeenBefore();
  if (seen) {
    // SE：装过但服务没在跑——故障态，话术是修复不是开启
    showState("se");
    aiActionMode = "start";
    setAiValue("amber", "未在运行");
    setText("aiDetail", "划线批注仍在正常保存；启动引擎后 AI 继续回应");
    const btn = $("aiActionBtn");
    btn.textContent = "启动";
    btn.classList.add("primary");
    $("consolePanel").hidden = true;
    $("startCmdBox").textContent = INSTALL_CMD;
    if (isWindowsPlatform()) {
      setText("startStepLabel", "Windows：按仓库 WINDOWS.md 的步骤重新运行 setup.ps1");
    }
  } else {
    // S0：新用户——首次配置是单向必做流程
    showState("s0");
    $("installCmdBox").textContent = INSTALL_CMD;
    if (isWindowsPlatform()) {
      setText("installStepLabel", "Windows：按仓库 WINDOWS.md 的步骤运行 setup.ps1，完成后回到这里");
    }
    setStatus("");
  }
  startHealthPolling();
}

async function renderOnline(data) {
  runtimeState = data;
  markEngineSeen();
  renderNotion(data);
  const ai = data.ai || {};
  if (ai.configured) {
    await renderS2(ai);
  } else {
    renderS1(ai);
  }
}

function renderS1(ai) {
  // 兜底态：引擎在线但没连上任何服务。只给 API Key 表单——
  // Claude Code / Codex 已确认不存在，三选一只是噪音。
  showState("s1");
  const claudeFound = Boolean(ai.available && ai.available.claude_code);
  const codexFound = Boolean(ai.available && ai.available.codex_cli);
  let probe =
    `已自动查找：Claude Code（${claudeFound ? "检测到" : "未检测到"}）` +
    `· Codex（${codexFound ? "检测到" : "未检测到"}）。` +
    "粘贴一个 API Key 就能开启。之后如果装了 Claude Code 或 Codex，在配置台一键切换。";
  if (ai.error && (claudeFound || codexFound)) {
    // 理论上检测到就会被自动连接；走到这说明连接出错，把真实原因给用户
    probe = `自动连接失败：${ai.error}。也可以先粘贴一个 API Key 开启。`;
  }
  setText("s1Probe", probe);
  $("s1QwenEndpointWrap").hidden = $("s1ApiProvider").value !== "qwen";
  $("s1ApiKey").placeholder = ai.apiKeySet ? "已保存；留空表示继续使用" : "粘贴 API Key";
  $("s1SaveBtn").disabled = !$("s1ApiKey").value.trim() && !ai.apiKeySet;
}

const PROVIDER_SHORT = { codex_cli: "Codex", claude_code: "Claude Code" };

function shortAiName(ai) {
  if (ai.selectedProvider === "api") return ai.displayName || "API 服务";
  return PROVIDER_SHORT[ai.selectedProvider] || ai.displayName || "AI 服务";
}

async function renderS2(ai) {
  showState("s2");
  $("startPanel").hidden = true;
  const paused = await readAiPaused();
  const btn = $("aiActionBtn");
  if (paused) {
    aiActionMode = "resume";
    setAiValue("amber", "已暂停");
    setText("aiDetail", "AI 暂停回应中，划线批注仍正常保存");
    btn.textContent = "恢复";
    btn.classList.add("primary");
    $("consolePanel").hidden = true;
  } else {
    aiActionMode = "console";
    setAiValue("green", `运行中 · ${shortAiName(ai)} 供能`);
    const source = ai.providerSource === "auto" ? "自动连接 · " : "";
    setText("aiDetail", `${source}${ai.detail || "记忆与数据都在本机"}`);
    btn.textContent = "详情";
    btn.classList.remove("primary");
  }
  setText("consoleSummary", `当前由 ${shortAiName(ai)} 提供模型能力${ai.detail ? ` · ${ai.detail}` : ""}`);
  $("useCodexBtn").disabled = !(ai.available && ai.available.codex_cli);
  $("useClaudeBtn").disabled = !(ai.available && ai.available.claude_code);
  $("useApiBtn").disabled = false;
  $("apiKey").placeholder = ai.apiKeySet ? "已保存；留空表示继续使用" : "粘贴 API Key";
  syncDraftFromRuntime(ai);
}

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

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
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
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

async function loadRuntimeStatus() {
  try {
    const data = await api("/config");
    await renderOnline(data);
  } catch (err) {
    await renderOffline();
  }
}

// ── AI 引擎行动作（详情 / 启动 / 恢复） ──

async function onAiAction() {
  if (aiActionMode === "start") {
    togglePanel("startPanel");
    return;
  }
  if (aiActionMode === "resume") {
    await setAiPaused(false);
    if (runtimeState) await renderS2(runtimeState.ai || {});
    setStatus("AI 已恢复回应", "");
    return;
  }
  togglePanel("consolePanel");
}

// ── S1 兜底：API Key 开启 ──

async function saveS1ApiConfig() {
  const apiProvider = $("s1ApiProvider").value;
  const payload = {
    provider: "api",
    apiProvider,
    apiKey: $("s1ApiKey").value.trim(),
    model: $("s1ApiModel").value.trim() || defaultApiModel(apiProvider),
    qwenEndpoint: $("s1QwenEndpoint").value,
  };
  setStatus("正在开启 AI...", "neutral");
  $("s1SaveBtn").disabled = true;
  try {
    await api("/config/ai", { method: "POST", body: JSON.stringify(payload) });
    const verified = await api("/config");
    $("s1ApiKey").value = "";
    await renderOnline(verified);
    setStatus("AI 已开启，去任何网页划一句话试试", "");
  } catch (err) {
    setStatus(err.message || "开启失败", "error");
    $("s1SaveBtn").disabled = false;
  }
}

// ── S2 配置台：切换服务 ──

function defaultApiModel(provider) {
  return provider === "openrouter" ? "openai/gpt-4o-mini" : "qwen3.5-plus";
}

function aiLabel(draftOrStatus) {
  const provider = draftOrStatus.provider || draftOrStatus.selectedProvider || draftOrStatus.providerConfig;
  if (provider === "codex_cli") return "Codex";
  if (provider === "claude_code") return "Claude Code";
  if (provider === "api") {
    return (draftOrStatus.apiProvider || "qwen") === "openrouter" ? "OpenRouter" : "千问 / Qwen";
  }
  return "AI 服务";
}

function currentAiProvider() {
  const ai = (runtimeState || {}).ai || {};
  const provider = ai.selectedProvider || ai.providerConfig || "";
  if (provider === "api") {
    return { provider: "api", apiProvider: ai.apiProvider || "qwen", model: ai.apiModel || "" };
  }
  if (provider === "claude_code") {
    return { provider, claudeBaseUrl: ai.claudeBaseUrl || "" };
  }
  return { provider };
}

function draftMatchesCurrent() {
  const current = currentAiProvider();
  if (aiDraft.provider !== current.provider) return false;
  if (aiDraft.provider === "api") {
    return aiDraft.apiProvider === current.apiProvider && (aiDraft.model || "") === (current.model || "");
  }
  if (aiDraft.provider === "claude_code") {
    return (aiDraft.claudeBaseUrl || "") === (current.claudeBaseUrl || "");
  }
  return true;
}

function syncDraftFromRuntime(ai) {
  const provider = ai.selectedProvider || ai.providerConfig || "codex_cli";
  if (provider === "api") {
    aiDraft = {
      provider: "api",
      apiProvider: ai.apiProvider === "openrouter" ? "openrouter" : "qwen",
      model: ai.apiModel || defaultApiModel(ai.apiProvider),
      qwenEndpoint: "qwen_cn",
      claudeBaseUrl: ai.claudeBaseUrl || "",
    };
  } else {
    aiDraft = {
      ...aiDraft,
      provider: provider === "claude_code" ? "claude_code" : "codex_cli",
      claudeBaseUrl: ai.claudeBaseUrl || "",
    };
  }
  syncAiDraftUi();
}

function setAiDraftProvider(provider) {
  aiDraft.provider = provider;
  if (provider === "api") {
    aiDraft.apiProvider = $("apiProvider").value;
    aiDraft.model = $("apiModel").value.trim() || defaultApiModel(aiDraft.apiProvider);
    aiDraft.qwenEndpoint = $("qwenEndpoint").value;
  } else if (provider === "claude_code") {
    aiDraft.claudeBaseUrl = $("claudeBaseUrl").value.trim();
  }
  syncAiDraftUi();
}

function syncAiDraftUi() {
  $("useCodexBtn").classList.toggle("is-selected", aiDraft.provider === "codex_cli");
  $("useClaudeBtn").classList.toggle("is-selected", aiDraft.provider === "claude_code");
  $("useApiBtn").classList.toggle("is-selected", aiDraft.provider === "api");
  $("claudeConfigFields").hidden = aiDraft.provider !== "claude_code";
  $("apiConfigFields").hidden = aiDraft.provider !== "api";
  $("claudeBaseUrl").value = aiDraft.claudeBaseUrl || "";
  $("apiProvider").value = aiDraft.apiProvider || "qwen";
  $("qwenEndpoint").value = aiDraft.qwenEndpoint || "qwen_cn";
  $("apiModel").value = aiDraft.model || defaultApiModel($("apiProvider").value);
  $("qwenEndpointWrap").hidden = $("apiProvider").value !== "qwen";

  const label = aiLabel(aiDraft);
  $("saveAiBtn").textContent = draftMatchesCurrent() ? `当前正在使用 ${label}` : `保存并切换到 ${label}`;
  $("saveAiBtn").disabled = draftMatchesCurrent();
  $("aiDraftHint").textContent = aiDraft.provider === "api"
    ? "API Key 只保存在这台电脑；已保存过 Key 时，留空会继续使用原来的 Key。"
    : "切换只影响模型供能，你的批注和记忆不受影响。";
}

function setAiSaving(isSaving) {
  for (const id of ["useCodexBtn", "useClaudeBtn", "useApiBtn", "claudeBaseUrl", "apiProvider", "qwenEndpoint", "apiModel", "apiKey", "saveAiBtn"]) {
    $(id).disabled = isSaving || (id === "saveAiBtn" && draftMatchesCurrent());
  }
  if (!isSaving) {
    $("useCodexBtn").disabled = !runtimeState?.ai?.available?.codex_cli;
    $("useClaudeBtn").disabled = !runtimeState?.ai?.available?.claude_code;
    $("useApiBtn").disabled = false;
    $("saveAiBtn").disabled = draftMatchesCurrent();
  }
}

async function saveAiConfig() {
  const payload = { provider: aiDraft.provider };
  if (aiDraft.provider === "api") {
    payload.apiProvider = $("apiProvider").value;
    payload.apiKey = $("apiKey").value.trim();
    payload.model = $("apiModel").value.trim() || defaultApiModel(payload.apiProvider);
    payload.qwenEndpoint = $("qwenEndpoint").value;
  } else if (aiDraft.provider === "claude_code") {
    payload.claudeBaseUrl = $("claudeBaseUrl").value.trim();
  }
  const label = aiLabel({ ...aiDraft, ...payload });
  setStatus(`正在切换到 ${label}...`, "neutral");
  setAiSaving(true);
  try {
    await api("/config/ai", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const verified = await api("/config");
    $("apiKey").value = "";
    $("consolePanel").hidden = true;
    await renderOnline(verified);
    setStatus(`已切换为 ${aiLabel((verified || {}).ai || aiDraft)}`, "");
  } catch (err) {
    setStatus(err.message || "切换失败", "error");
    syncAiDraftUi();
  } finally {
    setAiSaving(false);
  }
}

// ── Notion（真正可选的功能，保留开关语义） ──

async function onNotionSwitchChange() {
  const sw = $("notionSwitch");
  const notion = (runtimeState || {}).notion || {};
  if (sw.checked && !notion.saved && !notion.configured) {
    // 首次开启需要 Token：弹回，打开配置面板
    sw.checked = false;
    togglePanel("notionPanel");
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
    if (wantOn) togglePanel("notionPanel");
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

document.addEventListener("DOMContentLoaded", () => {
  $("notebookBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("src/notebook/index.html") });
    window.close();
  });

  // AI 引擎行：详情（S2）/ 启动（SE）/ 恢复（暂停中）
  $("aiActionBtn").addEventListener("click", onAiAction);
  $("aiRowInfo").addEventListener("click", onAiAction);
  $("closeConsoleBtn").addEventListener("click", () => {
    $("consolePanel").hidden = true;
    setStatus("");
  });
  $("closeStartPanelBtn").addEventListener("click", () => {
    $("startPanel").hidden = true;
    setStatus("");
  });

  // 安装 / 修复命令复制
  $("copyInstallCmdBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setStatus("已复制，去「终端」粘贴运行", "");
    } catch {
      setStatus("复制失败，请手动选中命令复制", "error");
    }
  });
  $("copyStartCmdBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setStatus("已复制，去「终端」粘贴运行", "");
    } catch {
      setStatus("复制失败，请手动选中命令复制", "error");
    }
  });

  // S1 兜底表单
  $("s1SaveBtn").addEventListener("click", saveS1ApiConfig);
  $("s1ApiKey").addEventListener("input", () => {
    $("s1SaveBtn").disabled = !$("s1ApiKey").value.trim() && !runtimeState?.ai?.apiKeySet;
  });
  $("s1ApiProvider").addEventListener("change", () => {
    $("s1QwenEndpointWrap").hidden = $("s1ApiProvider").value !== "qwen";
    $("s1ApiModel").value = defaultApiModel($("s1ApiProvider").value);
  });

  // 导出
  $("exportConfigBtn").addEventListener("click", () => togglePanel("exportPanel"));
  $("closeExportPanelBtn").addEventListener("click", () => {
    $("exportPanel").hidden = true;
    setStatus("");
  });
  $("exportMdBtn").addEventListener("click", () => exportVault("md"));
  $("exportJsonBtn").addEventListener("click", () => exportVault("json"));

  // 配置台：切换服务
  $("useCodexBtn").addEventListener("click", () => setAiDraftProvider("codex_cli"));
  $("useClaudeBtn").addEventListener("click", () => setAiDraftProvider("claude_code"));
  $("useApiBtn").addEventListener("click", () => setAiDraftProvider("api"));
  $("apiProvider").addEventListener("change", () => {
    aiDraft.provider = "api";
    aiDraft.apiProvider = $("apiProvider").value;
    $("apiModel").value = defaultApiModel($("apiProvider").value);
    aiDraft.model = $("apiModel").value;
    syncAiDraftUi();
  });
  $("qwenEndpoint").addEventListener("change", () => {
    aiDraft.provider = "api";
    aiDraft.qwenEndpoint = $("qwenEndpoint").value;
    syncAiDraftUi();
  });
  $("apiModel").addEventListener("input", () => {
    aiDraft.provider = "api";
    aiDraft.model = $("apiModel").value.trim();
    syncAiDraftUi();
  });
  $("apiKey").addEventListener("input", () => setAiDraftProvider("api"));
  $("claudeBaseUrl").addEventListener("input", () => {
    aiDraft.provider = "claude_code";
    aiDraft.claudeBaseUrl = $("claudeBaseUrl").value.trim();
    syncAiDraftUi();
  });
  $("saveAiBtn").addEventListener("click", saveAiConfig);

  // 暂停：配置台底部灰字 + 二次确认
  $("pauseLink").addEventListener("click", () => {
    $("pauseConfirm").hidden = !$("pauseConfirm").hidden;
  });
  $("cancelPauseBtn").addEventListener("click", () => {
    $("pauseConfirm").hidden = true;
  });
  $("pauseBtn").addEventListener("click", async () => {
    await setAiPaused(true);
    $("pauseConfirm").hidden = true;
    $("consolePanel").hidden = true;
    if (runtimeState) await renderS2(runtimeState.ai || {});
    setStatus("AI 已暂停，批注照常保存", "neutral");
  });

  // Notion
  $("notionSwitch").addEventListener("change", onNotionSwitchChange);
  $("notionRowInfo").addEventListener("click", () => { if (runtimeState) togglePanel("notionPanel"); });
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

  loadRuntimeStatus();
  loadVaultStats();
});
