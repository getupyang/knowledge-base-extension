# 知识库助手 — 开发上下文

**每次开新 session 必读完这份文件，再开始任何工作。**

---

## 一、产品愿景（不能忘）

这不是一个"保存网页"工具。

这是**创业方向「意图-行动缺口」的 L1 采集层**——当用户在浏览器阅读时划线的那一刻，是意图最真实的信号。插件捕捉这个信号，触发 AI 深度处理，把"看了但没做"变成"看了就做"。

**核心赌注：人的判断是稀缺资源，不应被自动化掉，应该被放大。**
用户划线/标注 = 人类索引。AI 全量捕获+结构化+检索 = 机器索引。两者叠加。

**北极星体验：**
用户在任意网页划线 → @agent 路由 → AI 立即深度处理 → 结果沉淀到本地 + Notion

用户本人是第一个用户。产品使用效果 = pitch 故事。

---

## 二、目录结构（2026-04-09 重构后）

```
knowledge-base-extension/          ← git repo 根目录
├── manifest.json                  ← Chrome 插件入口（加载整个目录即可）
├── setup.sh                       ← 首次安装（引导填 Notion 配置）
├── start.sh                       ← 每次启动两个服务
├── requirements.txt               ← Python 依赖
├── .kb_config.example             ← 配置模板（不含真实密钥）
├── README.md                      ← 对外安装说明
│
├── src/
│   ├── background/index.js        ← Notion API 调用、消息路由（密钥从 chrome.storage 读）
│   ├── content/index.js           ← 划线检测、评论区 UI、debug 面板
│   ├── collectors/claude.js       ← Claude.ai 对话采集（有 bug，低优先级）
│   ├── sidepanel/                 ← AI 对话侧边栏
│   └── popup/index.html           ← 插件配置页（填 Notion Token + DB ID）
│
├── backend/
│   ├── agent_api.py               ← Agent 后端（端口 8766）
│   ├── server.py                  ← 知识库浏览器（端口 8765）
│   ├── fetch_notion.py            ← 拉取 Notion 数据到本地缓存
│   ├── company_culture.md         ← L1 文化层：AI 输出行为规范（所有 agent 共用）
│   ├── project_context.template.md ← L2 项目层模板（用户自己填）
│   └── project_context.md         ← L2 项目层（不进 git，每人自己的）
│
└── docs/
    └── decisions/                 ← 设计决策存档
```

**不进 git 的文件：**
- `~/.kb_config`（密钥，chmod 600）
- `backend/comments.db`（SQLite，私人数据）
- `backend/project_context.md`（私人项目背景）
- `backend/.notion_cache.md`（Notion 缓存）

---

## 三、技术栈

- Chrome Extension Manifest V3，纯 JS（无构建工具）
- 后端：FastAPI（agent_api.py 端口 8766）+ 静态文件服务（server.py 端口 8765）
- 本地存储：SQLite = source of truth，路径 `backend/comments.db`
- 展示层：Notion（human-readable，不是主存储）
- AI：`claude -p --dangerously-skip-permissions`（绝对路径从 `~/.kb_config` 读）
- 密钥：后端走 `~/.kb_config`，插件走 `chrome.storage.local`，两者都不进 git

---

## 四、四层 Context 架构

每次 agent 调用注入的信息层级：

| 层 | 文件 | 状态 |
|---|---|---|
| L1 文化层 | `backend/company_culture.md` | ✅ 已上线 |
| L2 项目层（静态） | `backend/project_context.md` | ✅ 用户自己填 |
| L2 项目层（动态快照） | `backend/project_snapshot.md` | ❌ 待建 |
| L3 文章层 | Readability.js 提取当前页全文 | ❌ **P0，最高优先** |
| Notion 记忆 | 最近 15 条用户批注 | ✅ 已上线 |

**L3 是当前最高优先级**：agent 回答质量差的根本原因是不知道文章说了什么。

---

## 五、意图路由系统

| 触发词 | 人格 | 状态 |
|--------|------|------|
| @解释 | 翻译官：类比+局限，给可操作示例 | ⚠️ prompt 较通用 |
| @调研 | 侦察员：强制 WebSearch，事实/判断分层 | ⚠️ prompt 较通用 |
| @竞品 | 情报官：找差异化机会，不做功能列表 | ⚠️ prompt 较通用 |
| @思辨 | 苏格拉底：找假设、提反例，结尾必须是问号 | ⚠️ prompt 较通用 |
| @导师 | 发现盲区，一次只问一个灵魂问题 | ❌ 未实现 |

---

## 六、当前功能状态（2026-04-09）

| 功能 | 状态 |
|------|------|
| 任意网页划线 → 评论弹框 | ✅ |
| @agent 路由 → AI 回复 | ✅ |
| SQLite 持久化 | ✅ |
| Notion 异步写入 | ✅ |
| Debug 面板（elapsed/tokens/context） | ✅ |
| L1 文化层注入 | ✅ |
| L2 项目层注入 | ✅ |
| Notion 记忆注入（15条） | ✅ |
| L3 文章全文注入 | ❌ P0 |
| @导师 agent | ❌ P1 |
| 划线高亮持久化 | ❌ P1 |
| 任意网页稳定可用 | ❌ P2 |
| agent 人格重写（五个模板） | ❌ P2 |

---

## 七、数据流

```
用户划线（content.js mouseup）
    → 右键"评论" → 评论面板
    → 用户写评论（@xxx 路由）
    → submitComment()
        → 写 localStorage（本地持久化）
        → POST http://localhost:8766/comments
            → 注入 L1 + L2 + Notion 记忆 15 条
            → 后台线程：claude -p（绝对路径）
            → 结果写 SQLite replies 表
    → 前端轮询（3s × 30次 = 90s）
    → 展示 AI 回复 + debug 折叠面板
    → 异步写 Notion（展示层）
```

---

## 八、启动 & 验收命令

```bash
# 每次启动
bash ~/Documents/ai/coding/knowledge-base-extension/start.sh

# 验证服务
curl http://localhost:8765        # 知识库浏览器
curl http://localhost:8766/health # agent_api

# agent 全链路验收
COMMENT_ID=$(curl -s -X POST http://localhost:8766/comments \
  -H "Content-Type: application/json" \
  -d '{"page_url":"http://test.com","page_title":"测试","selected_text":"测试文本","comment":"@解释 这是验证"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
for i in $(seq 1 20); do
  R=$(curl -s http://localhost:8766/comments/$COMMENT_ID | python3 -c "
import json,sys; d=json.load(sys.stdin)
a=[r for r in d.get('replies',[]) if r['author']=='agent']
if a: print(a[-1]['content'][:100])
" 2>/dev/null); [ -n "$R" ] && echo "✓ $R" && break; echo "等待...$i"; sleep 3; done
```

---

## 九、优先级（下一个 session 从这里开始）

**P0：**
1. L3 文章上下文：Readability.js 提取页面全文，agent 调用时注入
2. agent 人格重写：按五个人格模板重写 AGENT_PROMPTS

**P1：**
3. @导师 Agent 实现
4. 划线高亮持久化
5. project_snapshot.md（L2 动态层）
6. setup.sh 自动写入 chrome.storage（朋友不用手动填两次配置）

**P2：**
7. 任意网页稳定可用
8. Claude.ai 采集器 bug 修复

---

## 十、开发规范

- 改了代码自己 curl 验证，不说"你试试"
- 遇到阻断（API 报错、CORS、权限）立即播报，不默默换方案
- 实现阶段不扩展需求，记录到优先级列表
- 修 bug：先复现 → 最小化修改 → 跑验收命令；3 次未解决停下来分析根因
- git 操作直接执行，不需要用户确认

---

## 十一、session 结束时

1. 更新本文件的"当前功能状态"和"优先级"
2. 更新 `~/research/topics/PROJECT.md`
3. 重要决策写入 `docs/decisions/`
4. 更新 memory index
