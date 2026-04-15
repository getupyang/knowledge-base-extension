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
| Claude Code | 最新 | `npm install -g @anthropic-ai/claude-code` |
| Chrome | 最新 | 用于加载插件 |
| Claude Pro 账号 | — | Claude Code 需要登录 |
| Notion 账号 | — | 用于存储批注 |

---

## 安装步骤

### 第一步：克隆代码

```bash
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
```

### 第二步：登录 Claude Code

```bash
claude login
```

按提示完成浏览器授权。验证登录成功：

```bash
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
- 初始化本地 SQLite 数据库
- 验证 Notion Token 是否有效

### 第五步：配置项目上下文（可选但推荐）

```bash
# 编辑 backend/project_context.md，填入你自己的背景信息
# 这让 AI 在回答时更了解你的工作场景
open backend/project_context.md
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
```

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
5. 结果自动展示在评论卡片中，同步写入 Notion

### 保存到知识库

选中文字 → 右键 → 「保存到知识库」→ 输入想法 → 直接存 Notion（无 AI 处理）

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
│   ├── fetch_notion.py         # 拉取 Notion 数据
│   ├── company_culture.md      # AI 输出行为规范（所有 agent 共用）
│   └── project_context.template.md  # 项目上下文模板（复制后自己填）
└── docs/
    └── decisions/              # 设计决策记录
```

---

## 常见问题

**Q: 发送评论后 AI 一直没有回复**

检查 agent_api 是否在运行：
```bash
curl http://localhost:8766/health
```
如果失败，重新运行 `bash start.sh`，查看日志：`backend/.logs/agent_api.log`

**Q: Notion 写入失败**

检查 Token 和 Database ID 是否正确，以及数据库是否已连接 Integration（步骤 3.3）。

**Q: `claude` 命令找不到**

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

**Q: 插件配置页保存后不生效**

刷新插件：`chrome://extensions` → 知识库助手 → 点击刷新按钮。

---

## 数据说明

- 所有批注和 AI 对话存储在本地 `backend/comments.db`（SQLite）
- Notion 是展示层，不是主存储
- `~/.kb_config` 存储你的密钥，`chmod 600` 权限，不会上传到 git
- 你的 Notion 数据库完全独立，和其他用户的数据互不干扰
