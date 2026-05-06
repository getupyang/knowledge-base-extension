# 知识库助手

# Knowledge Base Extension

**Turn reading into an AI workspace.**  
**从浏览文档出发，把阅读变成一个可协作的 AI 工作台。**

Knowledge Base Extension is a local-first Chrome extension that lets people highlight anything they read, leave thoughts in context, and turn those moments into structured AI work.

知识库扩展是一个本地优先的 Chrome 插件。  
它让用户在阅读过程中直接划线、记录想法，并把这些瞬间转化为结构化的 AI 工作流。

---

## The idea

Today, AI is powerful, but the way we work with it is still primitive.

We still copy text into chat boxes.  
We still restate context over and over.  
We still lose the thread between reading, thinking, asking, and reviewing.

This project explores a different interface:

**not chat first, but context first**  
**not another answer, but a working surface**

如今 AI 已经很强，但人与 AI 协作的界面仍然很原始。

我们还在把文字复制到聊天框里。  
还在一遍遍重讲上下文。  
还在阅读、思考、提问、验收之间不断断线。

这个项目想探索另一种界面：

**不是先聊天，而是先上下文**  
**不是多一个答案，而是多一个工作界面**

我的设计理念：

**产品解决的是思考的摩擦力问题。念头产生的地方就是处理它的地方。**

---

## Why it matters

The future AI product is not just “a smarter model.”

It is a system that can:

- stay inside the user’s real workflow
- accumulate context across time
- return work in a form people can inspect and improve
- feel less like prompting, and more like collaboration

未来的 AI 产品，不只是“更聪明的模型”。

它更应该是一个系统，能够：

- 留在用户真实的工作流里
- 跨时间积累上下文
- 以可检查、可改进的形式返回结果
- 更少像刻意的 prompting、skills，更多像协作 —— 如果还有human in the loop的话 ：）

---

## What this project is building

A new interaction layer between humans and AI:

- read in place
- think in place
- leave comments in place
- work with AI in place

This repo is an early prototype of that layer.

它想搭建的是一层新的人机协作界面：

- 在原地阅读
- 在原地思考
- 在原地留下评论
- 在原地与 AI 协作

这个仓库就是这层界面的一个早期原型。

---

## Current shape

Today, the system includes:

- a Chrome extension for highlighting and inline comments
- a local backend for storing notes and AI interactions
- a local knowledge browser
- optional Notion sync
- an experimental agent workflow around context-aware output

目前，这个系统包括：

- 一个支持划线和行内评论的 Chrome 插件
- 一个本地后端，用于保存笔记和 AI 交互
- 一个本地知识浏览器
- 可选的 Notion 同步
- 一个围绕上下文感知输出的实验性 Agent 工作流

---

## What is interesting here

This is **not** just a note-taking tool.  
It is **not** just another AI wrapper.  
It is an experiment in how AI could become part of the reading and thinking surface itself.

这**不是**一个普通笔记工具。  
这**不是**又一个 AI 壳。  
它更像是在实验：AI 能不能真正成为阅读与思考界面的一部分。

---

## Vision

We believe the next generation of AI products will not win only because they generate better text.

They will win because they create better **working environments** for thought, research, review, and collaboration.

我们相信，下一代 AI 产品的竞争，不会只发生在“谁生成得更好”。

更关键的是：谁能创造更好的**工作环境**，让思考、研究、验收和协作真正发生。

---

## Status

**Experimental. Local-first. Evolving quickly.**

This is an active prototype, not a finished product.

**实验阶段。本地优先。快速迭代中。**

这是一个正在生长中的原型，还不是最终产品。

---

## 前置要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| macOS | 12+ | 目前仅支持 Mac |
| Python 3 | 3.9+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| Chrome | 最新 | 用于加载插件 |
| Notion 账号 | — | 用于存储批注 |
| LLM 后端 | — | 二选一：OpenAI-compatible API key，或本地 Claude Code / Codex CLI |

可选本地后端：

- Claude Code：`npm install -g @anthropic-ai/claude-code`，适合已有 Claude 订阅的用户
- Codex CLI：适合已有 Codex 本地环境的用户

没有本地后端也能使用标准模式，配置 OpenRouter / OpenAI / DeepSeek / Kimi 等兼容 API 即可。

---

## 安装步骤

### 第一步：克隆代码

```bash
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
```

### 第二步：准备 LLM 后端

mem-ai 支持两种方式：

1. 本地后端：如果你已经安装并登录 Claude Code / Codex CLI，可以在安装脚本里固定选择一个后端，复用本地订阅额度。
2. API 标准模式：如果没有本地后端，在 `setup.sh` 中填写 OpenAI-compatible API key。

如果同一台机器同时有 Claude Code 和 Codex CLI，安装脚本会要求你手动选择一个固定后端。mem-ai 不会自动在二者之间切换；后续要切换时，修改 `~/.kb_config` 里的 `MEMAI_LLM_PROVIDER` 后重启。

Claude Code 用户可先登录：

```bash
claude login
claude --version
```

### 第三步：准备 Notion 数据库

**3.1 创建 Integration（获取 Token）**

1. 打开 https://www.notion.so/my-integrations
2. 点击「New integration」
3. 填写名称（如"知识库助手"），选择你的 Workspace
4. 创建后复制 **Internal Integration Token**（格式：`ntn_xxx...`）

**3.2 创建数据库**

1. 在 Notion 左侧栏点击 `+` 新建页面
2. 选择 **Database - Full page**（不是 Table、不是 Inline）
3. 给数据库起个名字（如"知识库"）
4. 添加以下字段（点击列头的 `+` 新增，**名称必须完全一致**）：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 标题 | Title | 默认已有，不用新建 |
| 来源平台 | Select | 新建列 → 类型选 Select |
| 来源URL | URL | 新建列 → 类型选 URL |
| 原文片段 | Text | 新建列 → 类型选 Text |
| 我的想法 | Text | 新建列 → 类型选 Text |
| 评论区对话 | Text | 新建列 → 类型选 Text |

**3.3 连接 Integration**

打开刚建的数据库页面 → 右上角 `···` → `Connections` → 选择你刚创建的 Integration

**3.4 获取 Database ID**

数据库页面的 URL 格式：
```
https://www.notion.so/你的名字/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=yyyyyyyy
                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                这部分是 Database ID（?v= 之前）
```
注意：`?v=` 后面的是 view ID，**不要复制错**。

### 第四步：运行安装脚本

```bash
bash setup.sh
```

脚本会：
- 检查所有依赖是否就绪
- 安装 Python 依赖（fastapi、uvicorn）
- 引导你输入 Notion Token 和 Database ID，写入 `~/.kb_config`
- 引导你固定选择一个 LLM 后端：Claude Code / Codex / API
- 初始化本地 SQLite 数据库
- 验证 Notion Token 是否有效

### 第五步：配置项目上下文（可选但推荐）

```bash
# 编辑这台电脑的本地私有项目背景
# 不填也可以，AI 会先基于本机 SQLite 里的批注逐步学习
open ~/.knowledge-base-extension/project_context.md
```

### 第六步：加载 Chrome 插件

1. 打开 Chrome，地址栏输入 `chrome://extensions`
2. 右上角开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择下载解压后的 `knowledge-base-extension` 文件夹（即包含 `manifest.json` 的那个目录）

插件图标出现在工具栏即为成功。点击插件图标，填入 Notion Token 和 Database ID（与 `~/.kb_config` 一致）。

---

## 每次启动

```bash
bash start.sh
```

看到以下输出说明就绪：

```
✓ 知识库服务器：http://localhost:8765
✓ Agent API：http://localhost:8766
✓ Worker：PID xxxxx
```

记忆笔记本有两个入口：

- 浏览器直接打开：http://localhost:8765/notebook/
- Chrome 插件弹窗 → 「打开记忆笔记本」

`start.sh` 会同时启动三部分：知识库浏览器、Agent API、后台 worker。worker 负责执行 `jobs` 表里的异步整理任务，例如「最近在想什么」和 rules → skills 蒸馏。

---

## 使用方法

### 划线评论（核心功能）

1. 在任意网页选中一段文字
2. 右键 → 「💬 评论」
3. 在弹框中输入你的想法，支持 `@` 路由到不同 AI 助手：
   - `@解释` — 解释这段内容，结合你的项目背景
   - `@调研` — 深度调研相关话题，强制多信息源
   - `@竞品` — 竞品分析，找差异化机会
   - `@思辨` — 多角度辩论，提出反例
4. 点击发送，AI 在后台处理（通常 30-60 秒）
5. 结果自动展示在评论卡片中，先写入本地 SQLite；如果配置了 Notion，会同步一份外部副本

### 保存到知识库

选中文字 → 右键 → 「保存到知识库」→ 输入想法 → 存入本地 SQLite（无 AI 处理）；如果配置了 Notion，会同步一份外部副本

---

## 目录结构

```
knowledge-base-extension/
├── setup.sh                    # 首次安装
├── start.sh                    # 每次启动
├── requirements.txt            # Python 依赖
├── .kb_config.example          # 配置文件模板
├── manifest.json               # Chrome 插件入口
├── src/
│   ├── background/index.js     # 插件后台：Notion API 调用、消息路由
│   ├── content/index.js        # 页面注入：划线检测、评论区 UI
│   ├── popup/index.html        # 插件配置页
│   └── sidepanel/              # AI 对话侧边栏
├── backend/
│   ├── agent_api.py            # Agent 后端（端口 8766）
│   ├── server.py               # 知识库浏览器（端口 8765）
│   ├── worker.py               # 后台异步任务 worker（jobs 表）
│   ├── agent_prompts/          # Agent / notebook 蒸馏 prompts
│   ├── fetch_notion.py         # 拉取 Notion 数据
│   ├── company_culture.md      # AI 输出行为规范（所有 agent 共用）
│   └── project_context.template.md  # 项目上下文模板（复制后自己填）
├── src/notebook/               # 记忆笔记本前端
└── docs/
    └── decisions/              # 设计决策记录
```

---

## 在另一台电脑部署

目标是让另一个用户在自己的电脑上运行同一套本地服务，并看到自己的记忆笔记本。

```bash
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
bash setup.sh
bash start.sh
```

然后：

1. 在 Chrome 开发者模式加载本仓库根目录。
2. 点击插件图标，填入这个用户自己的 Notion Token 和 Database ID。
3. 打开 http://localhost:8765/notebook/ 查看记忆笔记本。

数据是本地优先、按用户隔离的：

- `~/.knowledge-base-extension/comments.db` 是该用户的批注、对话、notebook generations。
- `~/.knowledge-base-extension/learned_rules.json` 是该用户从反馈里学到的规则。
- `~/.knowledge-base-extension/project_context.md` / `~/.knowledge-base-extension/user_profile.md` 是该用户自己的上下文。
- Agent 回复只使用这台电脑的本地记忆目录、本地 SQLite 和当前页面内容；代码仓库不携带任何人的记忆。

这些文件默认不进 git。新电脑新用户首次打开时，如果本地 `comments.db` 是空的、但该用户自己的 Notion database 里已有旧数据，记忆笔记本会自动做一次 Notion → SQLite 导入，共同日记随后就能看到旧行为。导入只用于兼容旧版本；新版运行时以本地 SQLite 为准。

如果要完整迁移另一台电脑上的本地状态，停服务后拷贝上述本地文件到同一路径即可。

---

## 常见问题

**Q: 发送评论后 AI 一直没有回复**

检查 agent_api 是否在运行：
```bash
curl http://localhost:8766/health
```
如果失败，重新运行 `bash start.sh`，查看日志：`~/.knowledge-base-extension/.logs/agent_api.log`

**Q: Notion 写入失败**

检查 Token 和 Database ID 是否正确，以及数据库是否已连接 Integration（步骤 3.3）。Notion 失败不影响本地 SQLite 的批注和共同日记。

**Q: Notion 里有旧数据，但共同日记是空的**

升级到最新版后重启 `bash start.sh`，打开记忆笔记本会自动导入一次旧 Notion 数据。也可以手动触发：

```bash
curl -X POST http://localhost:8766/notebook/import-notion \
  -H "Content-Type: application/json" \
  -d '{"limit":1000}'
```

**Q: AI 提到了不属于我的项目**

这属于数据隔离问题。新版已经不会信任旧版本可能带来的 maintainer 默认上下文；先升级并重启 `bash start.sh`。如果仍出现，检查并清空这台电脑上的私有上下文文件：

```bash
cat ~/.knowledge-base-extension/project_context.md
cat ~/.knowledge-base-extension/user_profile.md
cat ~/.knowledge-base-extension/learned_rules.json
```

**Q: 没有 Claude Code / Codex 能用吗？**

可以。运行 `bash setup.sh` 时填写 OpenRouter / OpenAI / DeepSeek / Kimi 等 OpenAI-compatible API key，即可使用标准模式。标准模式支持评论区回复、记忆笔记本、profile / skills / thinking 蒸馏；本地文件维护和长链路交付任务需要本地 agent 后端。

**Q: 我有 Claude Code，能不额外花 API 钱吗？**

可以。安装并登录后，`setup.sh` 会检测到 `claude`，默认优先走本地：

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

如果同时配置了 API key，本地后端失败时是否自动 fallback 到 API 由 `~/.kb_config` 里的 `MEMAI_LLM_FALLBACK=api|fail` 控制。默认是 `fail`，避免不知情地产生 API 成本。

**Q: 同时安装了 Claude Code 和 Codex，会自动选哪个？**

不会自动选。安装时必须固定选择一个：

```bash
MEMAI_LLM_PROVIDER=claude_code
# 或
MEMAI_LLM_PROVIDER=codex_cli
# 或
MEMAI_LLM_PROVIDER=api
```

切换后重新运行 `bash start.sh`。

**Q: 插件配置页保存后不生效**

刷新插件：`chrome://extensions` → 知识库助手 → 点击刷新按钮。

---

## 数据说明

- 所有批注和 AI 对话存储在本地 `~/.knowledge-base-extension/comments.db`（SQLite）
- Notion 是可选外部副本和旧数据导入来源，不是主存储
- `~/.kb_config` 存储你的密钥，`chmod 600` 权限，不会上传到 git
- 你的 Notion 数据库完全独立，和其他用户的数据互不干扰
