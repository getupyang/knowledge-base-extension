// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）

function $(id) {
  return document.getElementById(id);
}

async function loadRuntimeStatus() {
  const localStatus = $("localStatus");
  const backupStatus = $("backupStatus");
  const status = $("status");
  try {
    const res = await fetch("http://localhost:8766/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    localStatus.textContent = "已连接。所有批注、对话和记忆会持续保存在本机主库。";
    backupStatus.textContent = data.notionConfigured
      ? "Notion 外部备份已开启"
      : "Notion 外部备份未开启";
    status.style.color = "#1d7f5f";
    status.textContent = "本地优先模式";
  } catch (err) {
    localStatus.textContent = "未连接。请先运行 bash start.sh。";
    backupStatus.textContent = "后端离线时无法确认备份状态。";
    status.style.color = "#ef4444";
    status.textContent = "后端离线";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("notebookBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("src/notebook/index.html") });
    window.close();
  });
  loadRuntimeStatus();
});
