// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）

const API_BASE = "http://localhost:8766";

let runtimeState = null;
let aiDraft = {
  provider: "codex_cli",
  apiProvider: "qwen",
  model: "qwen3.5-plus",
  qwenEndpoint: "qwen_cn",
};

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

function togglePanel(id) {
  for (const panelId of ["aiPanel", "notionPanel"]) {
    const panel = $(panelId);
    panel.hidden = panelId === id ? !panel.hidden : true;
  }
}

function renderOffline() {
  setText("localStatus", "未连接");
  setText("localDetail", "请先运行 bash start.sh");
  setText("aiStatus", "无法确认");
  setText("aiDetail", "");
  setText("backupStatus", "无法确认");
  setText("backupDetail", "");
  $("aiConfigBtn").disabled = true;
  $("notionConfigBtn").disabled = true;
  setStatus("本地服务未启动", "error");
}

function renderRuntime(data) {
  runtimeState = data;
  const ai = data.ai || {};
  const notion = data.notion || {};

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
  return { provider };
}

function draftMatchesCurrent() {
  const current = currentAiProvider();
  if (aiDraft.provider !== current.provider) return false;
  if (aiDraft.provider === "api") {
    return aiDraft.apiProvider === current.apiProvider && (aiDraft.model || "") === (current.model || "");
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
    };
  } else {
    aiDraft = {
      ...aiDraft,
      provider: provider === "claude_code" ? "claude_code" : "codex_cli",
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
  }
  syncAiDraftUi();
}

function syncAiDraftUi() {
  $("useCodexBtn").classList.toggle("is-selected", aiDraft.provider === "codex_cli");
  $("useClaudeBtn").classList.toggle("is-selected", aiDraft.provider === "claude_code");
  $("useApiBtn").classList.toggle("is-selected", aiDraft.provider === "api");
  $("apiConfigFields").hidden = aiDraft.provider !== "api";
  $("apiProvider").value = aiDraft.apiProvider || "qwen";
  $("qwenEndpoint").value = aiDraft.qwenEndpoint || "qwen_cn";
  $("apiModel").value = aiDraft.model || defaultApiModel($("apiProvider").value);
  $("qwenEndpointWrap").hidden = $("apiProvider").value !== "qwen";

  const label = aiLabel(aiDraft);
  $("saveAiBtn").textContent = draftMatchesCurrent() ? `当前正在使用 ${label}` : `保存并切换到 ${label}`;
  $("saveAiBtn").disabled = draftMatchesCurrent();
  $("aiDraftHint").textContent = aiDraft.provider === "api"
    ? "API Key 只保存在这台电脑；已保存过 Key 时，留空会继续使用原来的 Key。"
    : "保存后，新的 AI 请求会使用这个服务。";
}

function setAiSaving(isSaving) {
  for (const id of ["useCodexBtn", "useClaudeBtn", "useApiBtn", "apiProvider", "qwenEndpoint", "apiModel", "apiKey", "saveAiBtn"]) {
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
});
