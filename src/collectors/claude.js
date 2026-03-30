// Claude.ai 对话自动采集

let lastSavedTurnCount = 0;
let saveTimer = null;

function getTurns() {
  const turns = [];

  // 用户消息
  const userMessages = document.querySelectorAll('[data-testid="user-message"]');
  // AI回复：包含 font-claude-response 的容器
  const aiMessages = document.querySelectorAll('.font-claude-response');

  const count = Math.min(userMessages.length, aiMessages.length);

  for (let i = 0; i < count; i++) {
    const userText = userMessages[i]?.innerText?.trim();
    const aiText = aiMessages[i]?.innerText?.trim();
    if (userText && aiText && aiText.length > 20) {
      turns.push({ user: userText, assistant: aiText });
    }
  }

  return turns;
}

function getConversationTitle() {
  return document.title?.replace(" - Claude", "").trim() || "Claude对话";
}

function isAIGenerating() {
  // 有停止按钮说明AI还在生成
  if (document.querySelector('[aria-label="Stop Response"]')) return true;
  // 最后一条AI回复还没有复制按钮，说明尚未生成完
  const copyButtons = document.querySelectorAll('[data-testid="action-bar-copy"]');
  const aiMessages = document.querySelectorAll('.font-claude-response');
  if (aiMessages.length > copyButtons.length) return true;
  return false;
}

async function sendToBackground(msg) {
  // 先ping一次唤醒service worker，再发真正的消息
  try {
    await chrome.runtime.sendMessage({ type: "PING" });
  } catch (e) {}
  return chrome.runtime.sendMessage(msg);
}

async function checkAndSaveNewTurns() {
  if (isAIGenerating()) return;

  const turns = getTurns();
  if (turns.length <= lastSavedTurnCount) return;

  const newTurns = turns.slice(lastSavedTurnCount);

  for (const turn of newTurns) {
    const aiConversation = `Q: ${turn.user}\n\nA: ${turn.assistant}`;
    try {
      const response = await sendToBackground({
        type: "SAVE_TO_NOTION",
        data: {
          title: getConversationTitle(),
          url: window.location.href,
          platform: "Claude",
          excerpt: truncate(turn.user, 200),
          thought: "",
          aiConversation
        }
      });
      if (response?.success) {
        console.log("[知识库] 已保存第", lastSavedTurnCount + newTurns.indexOf(turn) + 1, "轮对话");
      } else {
        console.error("[知识库] 保存失败", response?.error);
      }
    } catch (err) {
      console.error("[知识库] 保存失败", err);
    }
  }

  lastSavedTurnCount = turns.length;
}

// 监听DOM变化，防抖2秒
const observer = new MutationObserver(() => {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(checkAndSaveNewTurns, 4000);
});

function startObserving() {
  const target = document.querySelector('main') || document.body;
  observer.observe(target, { childList: true, subtree: true });

  // 页面已有对话时，先同步一次当前轮次数（不保存历史，只记录起点）
  const existingTurns = getTurns();
  lastSavedTurnCount = existingTurns.length;
  console.log("[知识库] Claude.ai采集器已启动，当前已有", lastSavedTurnCount, "轮对话（不重复保存）");
}

// SPA导航检测
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    lastSavedTurnCount = 0;
    setTimeout(() => {
      const existingTurns = getTurns();
      lastSavedTurnCount = existingTurns.length;
      console.log("[知识库] 页面导航，重置为", lastSavedTurnCount, "轮");
    }, 1500);
  }
}).observe(document, { subtree: true, childList: true });

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startObserving);
} else {
  startObserving();
}

function truncate(str, max) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "..." : str;
}
