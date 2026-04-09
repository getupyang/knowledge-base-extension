# 知识库助手

在任意网页划线，AI 立即深度处理，结果沉淀到本地 + Notion。

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

在 Notion 新建一个 Database（Full page），添加以下字段（名称必须一致）：

| 字段名 | 类型 |
|--------|------|
| 标题 | Title（默认已有） |
| 来源平台 | Select |
| 来源URL | URL |
| 原文片段 | Text |
| 我的想法 | Text |
| AI对话 | Text |

**3.3 连接 Integration**

打开刚建的数据库页面 → 右上角 `···` → `Connections` → 选择你的 Integration

**3.4 获取 Database ID**

数据库页面的 URL 格式：
```
https://www.notion.so/你的名字/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
```
其中那串 32 位字符就是 **Database ID**。

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
