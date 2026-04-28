// MV3 popup 脚本：内容必须外置，不能 inline（CSP 默认禁）
// 启动时回填配置 + 绑定按钮事件

document.addEventListener("DOMContentLoaded", () => {
  chrome.storage.local.get(["notionToken", "databaseId"], (result) => {
    if (result.notionToken) document.getElementById("notionToken").value = result.notionToken;
    if (result.databaseId) document.getElementById("databaseId").value = result.databaseId;
  });

  document.getElementById("notebookBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("src/notebook/index.html") });
    window.close();
  });

  document.getElementById("saveBtn").addEventListener("click", () => {
    const notionToken = document.getElementById("notionToken").value.trim();
    const databaseId = document.getElementById("databaseId").value.trim();
    const status = document.getElementById("status");

    if (!notionToken || !databaseId) {
      status.style.color = "#ef4444";
      status.textContent = "请填写 Notion Token 和 Database ID";
      return;
    }

    status.style.color = "#999";
    status.textContent = "保存中...";

    chrome.storage.local.set({ notionToken, databaseId }, () => {
      if (chrome.runtime.lastError) {
        status.style.color = "#ef4444";
        status.textContent = "保存失败：" + chrome.runtime.lastError.message;
        return;
      }
      chrome.storage.local.get(["notionToken", "databaseId"], (result) => {
        if (result.notionToken && result.databaseId) {
          chrome.runtime.sendMessage({ type: "RELOAD_CONFIG" });
          status.style.color = "#10b981";
          status.textContent = "✓ 已保存";
          setTimeout(() => status.textContent = "", 2000);
        } else {
          status.style.color = "#ef4444";
          status.textContent = "写入失败，请重试";
        }
      });
    });
  });
});
