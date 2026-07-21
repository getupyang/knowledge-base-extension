// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）

const API_BASE = "http://localhost:8766";

let runtimeState = null;
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

function togglePanel(id) {
  for (const panelId of ["aiPanel", "notionPanel", "exportPanel", "setupPanel"]) {
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

// ── 引擎状态机 ──
// S0 从未连接过本地服务（新用户）→ 「开启 AI」安装引导
// SE 连接过但现在不在跑        → 同一条安装命令兼作修复（install.sh 幂等）
// S1 服务在线但 AI 未配置       → 引导选择 AI 服务或贴 API Key
// S2 就绪                      → 正常状态
// 离线时每 2.5 秒探测 /health，装好后 popup 自动点亮，用户不需要刷新。

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
        $("setupPanel").hidden = true;
        loadRuntimeStatus();
      }
    } catch { /* 继续等 */ }
  }, 2500);
}

async function renderOffline() {
  const seen = await engineSeenBefore();
  setText("aiStatus", "未开启");
  setText("aiDetail", "");
  setText("backupStatus", "—");
  setText("backupDetail", "");
  $("aiConfigBtn").disabled = true;
  $("notionConfigBtn").disabled = true;

  const setupBtn = $("setupBtn");
  setupBtn.hidden = false;
  if (seen) {
    // SE：装过，但服务没在跑
    setText("localStatus", "未连接");
    setText("localDetail", "本地服务没有在运行");
    setupBtn.textContent = "修复";
    setText("setupTitle", "恢复本地服务（约 1 分钟）");
    setText("setupIntro", "重新运行一次安装命令即可恢复（顺便自动更新到最新版），批注数据不受影响。");
    setStatus("本地服务未运行", "error");
  } else {
    // S0：新用户，从未连接过
    setText("localStatus", "划线批注可用");
    setText("localDetail", "开启 AI 后，批注会得到回应，并被长期记住");
    setupBtn.textContent = "开启 AI";
    setText("setupTitle", "开启 AI（约 2 分钟）");
    setStatus("");
  }
  if (isWindowsPlatform()) {
    setText("setupIntro", "Windows 安装请按仓库 WINDOWS.md 的步骤运行 setup.ps1；完成后回到这里，状态会自动变绿。");
  }
  $("installCmdBox").textContent = INSTALL_CMD;
  startHealthPolling();
}

function renderRuntime(data) {
  runtimeState = data;
  const ai = data.ai || {};
  const notion = data.notion || {};

  markEngineSeen();
  $("setupBtn").hidden = true;
  setText("localStatus", "已连接");
  setText("localDetail", "批注、对话和记忆会保存在这台电脑上");

  setText("aiStatus", ai.displayName || "未配置");
  setText("aiDetail", ai.error || ai.detail || "");

  if (notion.configured || data.notionConfigured) {
    setText("backupStatus", "Notion 已开启");
    setText("backupDetail", "会额外保存一份云端备份");
  } else if (notion.saved) {
    setText("backupStatus", "Notion 已关闭");
    setText("backupDetail", "已记住配置，需要时可重新开启");
  } else {
    setText("backupStatus", "未开启");
    setText("backupDetail", "本机保存已可用，可按需配置 Notion");
  }

  $("aiConfigBtn").disabled = false;
  $("notionConfigBtn").disabled = false;
  $("useCodexBtn").disabled = !ai.available?.codex_cli;
  $("useClaudeBtn").disabled = !ai.available?.claude_code;
  $("useApiBtn").disabled = false;
  $("apiKey").placeholder = ai.apiKeySet ? "已保存；留空表示继续使用" : "粘贴 API Key";
  syncNotionForm(notion);
  syncDraftFromRuntime(ai);
  setStatus("本地优先模式", "neutral");
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
    renderRuntime(data);
  } catch (err) {
    renderOffline();
  }
}

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
      qwenEndpoint: ai.apiProvider === "openrouter" ? "qwen_cn" : "qwen_cn",
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
    : aiDraft.provider === "claude_code"
      ? "保存后，新的 Claude Code 请求会使用这个 Base URL。留空则使用默认配置。"
      : "保存后，新的 AI 请求会使用这个服务。";
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
    renderRuntime(verified);
    $("apiKey").value = "";
    setStatus(`已切换为 ${aiLabel((verified || {}).ai || aiDraft)}`, "");
  } catch (err) {
    setStatus(err.message || "切换失败", "error");
    syncAiDraftUi();
  } finally {
    setAiSaving(false);
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
    renderRuntime({ ...(runtimeState || {}), notion: data.notion, notionConfigured: data.notion?.configured });
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
    renderRuntime({ ...(runtimeState || {}), notion: data.notion, notionConfigured: false });
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
    renderRuntime({ ...(runtimeState || {}), notion: data.notion, notionConfigured: data.notion?.configured });
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
  $("aiConfigBtn").addEventListener("click", () => togglePanel("aiPanel"));
  $("notionConfigBtn").addEventListener("click", () => togglePanel("notionPanel"));
  $("exportConfigBtn").addEventListener("click", () => togglePanel("exportPanel"));
  $("setupBtn").addEventListener("click", () => togglePanel("setupPanel"));
  $("closeSetupPanelBtn").addEventListener("click", () => {
    $("setupPanel").hidden = true;
    setStatus("");
  });
  $("copyInstallCmdBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setStatus("已复制，去「终端」粘贴运行", "");
    } catch {
      setStatus("复制失败，请手动选中命令复制", "error");
    }
  });
  $("closeExportPanelBtn").addEventListener("click", () => {
    $("exportPanel").hidden = true;
    setStatus("");
  });
  $("exportMdBtn").addEventListener("click", () => exportVault("md"));
  $("exportJsonBtn").addEventListener("click", () => exportVault("json"));
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
  $("saveNotionBtn").addEventListener("click", saveNotionConfig);
  $("disableNotionBtn").addEventListener("click", disableNotionConfig);
  $("createNotionDatabaseBtn").addEventListener("click", createNotionDatabase);
  $("openNotionIntegrationsBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: "https://www.notion.so/my-integrations" });
  });
  $("closeAiPanelBtn").addEventListener("click", () => {
    $("aiPanel").hidden = true;
    setStatus("");
  });
  $("closeNotionPanelBtn").addEventListener("click", () => {
    $("notionPanel").hidden = true;
    setStatus("");
  });
  loadRuntimeStatus();
  loadVaultStats();
});
