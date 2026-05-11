# Margin

在任意网页上划线并评论，写下你的思考和疑问。无需切换页面，Margin 会在右侧回应你，并把原文与现场思考一起记住，让 AI 下次带着你的思路、判断习惯和项目背景继续回答。

Margin 是一个本地优先的 Chrome 插件。你在网页、论文、日报或 AI 对话里看到重要内容时，可以直接划线、写评论、召唤 AI 回复。Margin 会把这些现场互动保存下来，让之后的回复更懂你的项目、判断标准和做事方式。

> 仓库名暂时仍是 `knowledge-base-extension`，产品名是 `Margin`。

## 你可以用它做什么

- 在任意网页上划线，把想法留在原文旁边。
- 对一段文字直接追问 AI，不用复制粘贴到新聊天窗口。
- 让 AI 记住你的纠正、偏好和长期关注点。
- 打开记忆笔记本，回看最近的项目、问题、主题和已养成的工作方式。
- 默认把数据保存在本机，Notion 只是可选外部备份。

## 三步安装

### 1. 下载代码并初始化

```bash
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
bash onboard.sh
```

`onboard.sh` 会检查运行环境、初始化本地数据库、引导你选择一个模型服务，并询问是否开启 Notion 外部备份。Notion 可以不填，不影响本地使用。

### 2. 把插件加载到 Chrome

1. 打开 `chrome://extensions`
2. 右上角开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择这个仓库目录，也就是包含 `manifest.json` 的文件夹

看到工具栏出现 Margin 图标，就说明插件已加载。

### 3. 启动本地工作台

如果 onboarding 已经自动启动，通常不用再手动运行。需要手动启动时：

```bash
bash start.sh
```

看到下面几行说明就绪：

```text
✓ 知识库服务器：http://localhost:8765
✓ Agent API：http://localhost:8766
✓ Worker：PID xxxxx
```

## 第一次使用

1. 打开任意网页。
2. 选中一段你想留下来的文字。
3. 右键选择「评论」，或者使用页面右侧的评论面板。
4. 写下你的想法、问题或纠正。
5. 需要 AI 回答时，点击「请 AI 回复」。

AI 回复会出现在原文旁边。下一次你再问相关问题时，Margin 会尝试带上这条评论和你的历史上下文。

## 记忆笔记本

记忆笔记本有两个入口：

- 点击 Chrome 工具栏里的 Margin 图标，再点「打开记忆笔记本」
- 直接打开 `http://localhost:8765/notebook/`

你可以在里面查看：

- 最近批注过的页面和评论
- 「你 & 项目」的浓缩状态
- 当前正在升温的问题和主题
- AI 从你的反馈里学到的工作方式
- 未读 AI 回复

## 数据保存在哪里

主数据默认保存在本机：

```text
~/.knowledge-base-extension/comments.db
```

这里包含你的批注、评论、AI 回复、记忆事件和 notebook 数据。仓库里的代码不会携带任何人的私有记忆。

如果你在 `onboard.sh` 里配置了 Notion，它只作为外部备份或旧数据导入通道；本地 SQLite 仍然是默认主库。

换机器前，建议先停止服务，然后备份整个目录：

```text
~/.knowledge-base-extension/
```

## 每次重启电脑后

如果你没有开启开机自动恢复，手动运行：

```bash
cd knowledge-base-extension
bash start.sh
```

如果想补开启机自动恢复：

```bash
bash start.sh --install-login-item
```

## 更新到最新版

```bash
cd knowledge-base-extension
git pull
bash onboard.sh
bash start.sh
```

然后到 `chrome://extensions` 刷新 Margin 插件，并刷新已经打开的网页。

## 常见问题

### 插件加载后没有反应

先确认本地服务已启动：

```bash
bash start.sh
```

再刷新：

1. `chrome://extensions` 里的 Margin 插件
2. 你正在阅读的网页

### 发送评论后 AI 一直没有回复

运行健康检查：

```bash
scripts/kb-health
```

重点看 `Agent API`、`Worker`、`LLM provider` 是否正常。如果刚切换模型服务，重新运行：

```bash
bash onboard.sh
```

### Notion 没配置会不会影响使用

不会。Margin 默认使用本机 SQLite。Notion 只是可选外部备份，不是必需账号。

### AI 提到了不属于我的项目

先在记忆笔记本里检查相关评论和项目状态；如果是错误记忆，可以继续在原评论旁纠正它。Margin 会把纠正当成比普通高亮更强的信号。

## 开发者入口

常用检查：

```bash
scripts/kb-health
git diff --check
node --check src/content/index.js
node --check src/notebook/app.js
```

本地回归：

```bash
scripts/kb-regression
```

目录概览：

```text
manifest.json              Chrome extension 配置
src/content/               页面划线、评论面板、AI 回复 UI
src/popup/                 工具栏弹窗
src/notebook/              记忆笔记本前端
backend/                   本地 Agent API、SQLite、worker
scripts/kb-health          健康检查
scripts/kb-regression      回归检查
onboard.sh                 首次配置
start.sh                   启动本地工作台
```
