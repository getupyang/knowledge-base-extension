# 讨论存档：系统架构 + 产品方向

**日期：** 2026-04-08 ~ 2026-04-09
**性质：** 方向性讨论 + 工程重构
**状态：** 已落地，部分 todo 待执行

---

## 本次做了什么

### 1. 建立 PROJECT.md（系统总览）

路径：`~/research/topics/PROJECT.md`

把整个系统从"散落在记忆里"变成"有文档的架构"。包含：
- L0～L6 分层架构图
- 各子项目状态（插件、Book Scanner、VoiceInput、Dev Digest）
- Context Engineering 设计
- P0/P1 优先级
- 参考产品对照表

### 2. Granola 深度调研

路径：`~/research/topics/intention-action-gap/competitive-research/product-research-granola-20260408.md`

**关键发现：**
- Granola aha moment 演变：在场感（2024）→ 知识库（2025）→ 公司记忆（2026）
- 核心哲学："writing is thinking"，人写索引，机器写细节
- $1.5B 估值，70%+ 周留存，一年内从 $250M 涨到 $1.5B
- 2026 年推出 Spaces（跨会议知识库）+ MCP server（让 Claude/ChatGPT 接入 Granola 数据）

**对我们项目的启发：**
- 我们的划线/标注 = Granola 的人类笔记，是核心机制，不是附加功能
- 增长飞轮：好的阅读标注 → 更相关的 AI 分析 → 用户更愿意继续用 → 数据积累
- MCP 接口层是未来方向：让知识库成为其他 AI 工具的上下文来源
- 冷启动问题真实存在：前三个月没有数据积累时，AI 体验不会令人惊艳

### 3. Repo 重构为可分发结构

**背景：** 代码散落在两个目录（`~/research/` 和 `~/Documents/ai/coding/knowledge-base-extension/`），密钥硬编码导致一直无法推 GitHub。

**执行：**
- 后端文件（agent_api.py、server.py 等）迁入 `backend/` 子目录
- 删除所有 HARDCODED_CONFIG 和 OpenRouter 配置
- 清理 git 历史中的所有 token（filter-branch）
- 推送到 GitHub：https://github.com/getupyang/knowledge-base-extension
- 写 README，朋友 clone 后 6 步可用

**朋友安装流程：**
```
git clone → claude login → 建 Notion DB → bash setup.sh → Chrome 加载插件 → bash start.sh
```

**已知待优化：** setup.sh 填的配置和插件 popup 填的配置是两套（后端走 `~/.kb_config`，插件走 `chrome.storage`），朋友要填两次。

---

## 金句 / 值得记录的洞察

**"机器做机器的索引，我做我的索引"**
这是本项目的核心设计哲学，和 Granola 的"writing is thinking"本质相同。

**"浴室里的灵光一现"**
用户描述的 aha moment：反复琢磨一件事，突然有一天想通了。产品要做的是让 AI 陪伴用户"持续反复琢磨"——每一篇新文章过来，都带着过去所有积累的问题。厚积薄发。

这很难 scale，也很低频，但这是真实的产品愿景。

**"共享的不是知识，而是这套工作模式"**
用户对分发策略的定义：每个人部署一套，用自己的 Notion 数据库，AI 越来越懂自己。不是共享知识库，是共享一套让 AI 懂自己的方法论。

**"飞书一下子就会碾压我们（但我们做的是全网的，不光是飞书的）"**
用户的竞争视角：场景聚焦的产品（飞书文档评论区 agent）会在单场景碾压，但全网覆盖是我们的壁垒。不要在别人的场景里打。

---

## 待讨论 / 未解决的 TODO

### 技术层面

- [ ] **setup.sh 自动写 chrome.storage**：让朋友只填一次配置，不用在终端和插件里各填一遍
- [ ] **L3 文章全文注入**：用 Readability.js 提取当前页全文，这是当前 AI 回答质量最大的瓶颈
- [ ] **Andrej Karpathy 的 gist 值得深看**：他的"本地 SQLite + 向量搜索 + LLM 召回"脚本产品化思路，和我们做的高度重合
- [ ] **MCP server 方向**：Granola 2026 年优先做"成为上下文层"，我们也该考虑把知识库做成其他 AI 工具可以调用的上下文来源

### 产品层面

- [ ] **冷启动设计**：前三个月用户没有历史积累时，价值主张是什么？不能全靠"越用越聪明"
- [ ] **Book Scanner 开发**：设计文档已完成（`~/research/topics/book-scanner/`），待技术选型和开发
- [ ] **开源项目调研**：Context7、Mem0、Screenpipe、Obsidian Clipper 等，找哪些轮子不用重造（建议开新 session）

### 方向层面

- [ ] **AirJelly 竞品体验**：todo 里一直没做，字节系最接近竞品，需要注册体验
- [ ] **Notion 迁移到本地 Obsidian**：用户提到 Andrej 的方式（本地缓存 URL 内容），是否该把 Notion 内容迁移到 Obsidian + Clipper？需要讨论
- [ ] **向量化知识层（L4）**：当前数据积累后，下一步是 ChromaDB/LanceDB 本地向量化，让 AI 能跨文章语义关联

---

## 文件变更清单（本次 session）

| 文件 | 操作 |
|------|------|
| `~/research/topics/PROJECT.md` | 新建，系统总览 |
| `~/research/topics/intention-action-gap/competitive-research/` | 新建目录，竞品调研统一归档 |
| `~/research/topics/intention-action-gap/competitive-research/product-research-granola-20260408.md` | 新建，Granola 深度调研 |
| `knowledge-base-extension/backend/` | 新建目录，迁入后端服务文件 |
| `knowledge-base-extension/setup.sh` | 重写，路径指向 backend/ |
| `knowledge-base-extension/start.sh` | 重写，路径指向 backend/ |
| `knowledge-base-extension/src/background/index.js` | 删除 HARDCODED_CONFIG 和 OpenRouter |
| `knowledge-base-extension/src/popup/index.html` | 删除 OpenRouter 字段，修复验证逻辑 |
| `knowledge-base-extension/README.md` | 新建，对外安装说明 |
| `knowledge-base-extension/CLAUDE.md` | 重写，更新路径和状态 |
| `knowledge-base-extension/docs/decisions/2026-04-09-repo-restructure.md` | 新建，决策记录 |
| `~/.claude/projects/memory/feedback_pmo_contract.md` | 更新，加 git 操作不需要确认 |
| `~/.claude/projects/memory/project_knowledge_base_extension.md` | 更新，架构和状态 |
| `~/.claude/projects/memory/project_personal_os.md` | 更新，精简，指向 PROJECT.md |
| GitHub repo | 推送，git 历史密钥全部清除 |
