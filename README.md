**中文** | [English](README_EN.md)

<p align="center">
  <img src="assets/icons/icon128.png" width="96" alt="Margin 图标" />
</p>

# Margin

**在网页上划线，就能问 AI。**

Margin 是一个本地优先的浏览器边注 AI。你在网页、论文、GitHub、日报或 AI 对话里划线评论，AI 会在页面右侧回答；批注、回复和记忆默认保存在你自己的电脑上。

[官网](https://get-margin.vercel.app) · [快速开始](#-快速开始) · [功能介绍](#-功能介绍) · [隐私说明](#-隐私说明) · [常见问题](#-常见问题)

---

## 为什么选择 Margin？

| 能力 | Margin | 普通 AI 聊天 | 传统笔记工具 |
| --- | --- | --- | --- |
| 在原文旁边提问 | ✅ | ❌ 需要复制粘贴 | 部分 |
| 网页自动成为聊天上下文 | ✅ | ❌ 需要重讲背景 | ❌ |
| 批注、回复、记忆默认本地保存 | ✅ | ❌ | 部分 |
| 支持让 AI 记住你的纠正和做事方式 | ✅ | 部分 | ❌ |
| 把追问沉淀成长期上下文 | ✅ | ❌ 容易留在单次对话里 | 部分 |
| 下次回答能接上你之前的判断 | ✅ | 部分，取决于平台记忆 | ❌ |
| 在记忆笔记本里回看证据和变化 | ✅ | ❌ | 部分 |
| 支持 macOS / Windows / WSL | ✅ | ✅ | ✅ |
| 可选 Notion 外部备份 | ✅ | ❌ | 部分 |
| 开源，可自己检查 | ✅ | ❌ | 部分 |

---

## 它解决什么问题？

今天和 AI 协作，最麻烦的不是模型不够聪明，而是上下文一直断：

- 看到一段话，要复制到聊天窗口。
- 问完一次，下次又要重讲背景。
- 灵感、反驳、纠正散落在不同工具里。
- AI 很会回答，但不一定记得你为什么在意。

Margin 的核心想法很简单：

**念头在哪里产生，AI 就应该在哪里接住它。**

你不用离开当前页面。划线、评论、提问，AI 直接带着原文上下文回答。之后这些评论和回复还能进入记忆笔记本，变成 AI 下次理解你的依据。

---

## 一分钟理解

1. 打开一篇你正在读的网页。
2. 划中一句让你停下来的话。
3. 写一句评论，记录你此刻的好奇、追问、或自己的判断和感受。
4. Margin 在右侧回复，并保存原文、你的评论和 AI 回复。
5. 之后 AI 回答相关问题时，可以带上这段背景，而不是让你重讲。

---

## 功能介绍

### 划线评论

在任意网页选中文本，打开 Margin 评论入口，写下一句真实问题：

```text
这段为什么重要？
```

AI 会带着当前原文回答，而不是让你重新复制上下文。

### 网页变成聊天上下文

Margin 不是把网页当成孤立笔记保存。你在哪段原文旁边提问，AI 就带着那句话、页面和你的评论回答。每个网页都能变成你和 AI 的现场对话。

### 记住你的方法

评论、纠正和追问是很强的个人信号。Margin 会把这些信号沉淀到本地记忆里，让 AI 逐渐知道：

- 你在研究什么问题
- 你喜欢什么解释方式
- 哪些判断是你反复强调的
- 哪些规则应该下次直接复用

### 记忆笔记本

本地知识浏览器默认运行在：

```text
http://localhost:8765/notebook/
```

你可以在这里回看批注、AI 回复、近期主题、项目上下文和逐步形成的工作习惯。

### 可选 Notion 备份

Notion 不是必需项。Margin 默认使用本地 SQLite。你可以选择把批注同步到自己的 Notion 数据库，作为外部备份或旧数据导入来源。

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
```

### 2. 运行安装脚本

macOS / WSL：

```bash
bash setup.sh
bash start.sh
```

Windows PowerShell：

```powershell
.\setup.ps1
.\start.ps1
```

Windows CMD：

```bat
setup.cmd
start.cmd
```

安装脚本会引导你选择 AI 服务：

| 方式 | 适合谁 |
| --- | --- |
| Claude Code | 已安装并登录 Claude Code 的用户 |
| Codex CLI | 已安装并登录 Codex CLI 的用户 |
| 千问 / Qwen API | 有阿里云百炼或 Qwen API Key 的用户 |
| OpenRouter API | 有 OpenRouter API Key 的用户 |

### 3. 加载 Chrome 插件

1. 打开 `chrome://extensions`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本仓库根目录，也就是包含 `manifest.json` 的目录

看到 Margin 图标出现在 Chrome 工具栏，就可以打开网页开始划线评论。

---

## 每次启动

macOS / WSL：

```bash
bash start.sh
```

Windows PowerShell：

```powershell
.\start.ps1
```

Windows CMD：

```bat
start.cmd
```

看到下面输出说明本地服务已就绪：

```text
✓ 知识库服务器：http://localhost:8765
✓ Agent API：http://localhost:8766
✓ Worker：PID xxxxx
```

---

## 隐私说明

Margin 接触的是你的阅读、批注、追问和纠正，所以默认策略必须足够保守。

| 数据 | 默认位置 | 说明 |
| --- | --- | --- |
| 批注和 AI 回复 | `~/.knowledge-base-extension/comments.db` | 本机 SQLite |
| 本地备份 | `~/.knowledge-base-extension/backups/` | 用于恢复，不是云端同步 |
| 项目背景 | `~/.knowledge-base-extension/project_context.md` | 你自己填写，可为空 |
| 用户偏好 | `~/.knowledge-base-extension/user_profile.md` | 本地私有文件 |
| 密钥配置 | `~/.kb_config` | 本机配置文件，不进 Git |
| Notion | 可选 | 只在你主动配置后使用 |

Margin 不默认扫描你的全量浏览历史。它更信任你主动划线、评论、纠正这些高置信动作。只看过一个页面，不等于你认同它，也不等于它应该变成记忆。

---

## 最近更新

- **v0.3.12** — 支持微信读书 Web reader 页选区评论；正文是 canvas/custom selection 时，仍可把选中文本带入右侧评论面板。
- **Windows 支持** — 支持 PowerShell / CMD 启动脚本；WSL 用户继续在 WSL 内使用 bash 脚本，不混用终端环境。
- **本地优先笔记本** — 本地 SQLite 是主数据源；Notion 作为可选备份和旧数据导入来源。

---

## 技术架构

| 层级 | 说明 |
| --- | --- |
| Chrome 插件 | 划线检测、评论入口、右侧评论面板、popup |
| Content script | 注入网页，处理选区、锚点、评论 UI |
| Agent API | 本地后端，默认端口 `8766` |
| 知识库浏览器 | 本地阅读和记忆笔记本，默认端口 `8765` |
| Worker | 处理后台异步任务和记忆增长 |
| SQLite | 本地批注、回复、notebook 数据 |
| 可选 Notion | 外部备份和旧数据导入 |

---

## 常见问题

### 不配置 Notion 会丢数据吗？

不会。主数据默认保存在本机 `~/.knowledge-base-extension/comments.db`。Notion 只是可选外部副本。

### 没有 Claude Code / Codex 能用吗？

可以。安装时选择千问 / Qwen API 或 OpenRouter API 即可。

### 为什么 AI 没有回复？

先检查本地 Agent API：

```bash
curl http://localhost:8766/health
```

如果失败，重新运行启动脚本。日志在：

```text
~/.knowledge-base-extension/.logs/agent_api.log
```

Windows 日志在：

```text
$HOME\.knowledge-base-extension\.logs\agent_api.log
```

### 插件更新后不生效怎么办？

打开 `chrome://extensions`，点击 Margin 的刷新按钮，然后刷新已经打开的网页。

### AI 提到了不属于我的项目怎么办？

这通常是本机私有上下文或旧数据导入问题。检查这些文件：

```bash
cat ~/.knowledge-base-extension/project_context.md
cat ~/.knowledge-base-extension/user_profile.md
cat ~/.knowledge-base-extension/learned_rules.json
```

如果你是新用户、空数据库，Margin 不应该借用开发者或其他用户的默认记忆。

---

## 目录结构

```text
knowledge-base-extension/
├── manifest.json              # Chrome 插件入口
├── setup.sh / start.sh        # macOS / WSL 安装和启动
├── setup.ps1 / start.ps1      # Windows PowerShell 入口
├── setup.cmd / start.cmd      # Windows CMD 入口
├── src/
│   ├── content/               # 页面注入、划线、评论 UI
│   ├── background/            # 插件后台
│   ├── popup/                 # 插件弹窗
│   ├── sidepanel/             # 侧边栏
│   └── notebook/              # 记忆笔记本前端
├── backend/
│   ├── server.py              # 本地知识库浏览器，端口 8765
│   ├── agent_api.py           # 本地 Agent API，端口 8766
│   ├── worker.py              # 后台任务 worker
│   └── llm_client.py          # LLM provider 适配
└── scripts/
    ├── kb-health              # 本地健康检查
    └── kb-regression          # 回归检查
```

---

## 反馈与交流

- 官网：[https://get-margin.vercel.app](https://get-margin.vercel.app)
- 问题反馈：[GitHub Issues](https://github.com/getupyang/knowledge-base-extension/issues)

如果你在安装、Windows、隐私或本地数据保存上遇到问题，也可以扫码加我微信，备注 `Margin`。

<p align="center">
  <img src="assets/contact/wechat-qr.jpg" width="220" alt="微信二维码" />
</p>
