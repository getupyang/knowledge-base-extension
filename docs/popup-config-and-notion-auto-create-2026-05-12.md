# 2026-05-12 · Popup 配置入口与 Notion 自动建库

- 记录时间：2026-05-12
- 关联 commit：`5db1433`（已推 `origin/main`）
- 分支：`main`
- 涉及文件：`src/popup/index.html`、`src/popup/popup.js`、`backend/agent_api.py`

## 改了什么

Popup 从单纯状态展示，变成用户可理解的配置入口：

- `AI 服务` 卡片可以展开切换服务，也可以 `收起`。
- `云端备份` 卡片可以展开配置 Notion，也可以 `收起`。
- Notion 配置支持粘贴一个已授权 integration 的页面链接后自动创建数据库。
- `关闭` 语义改成 `暂停云端备份`，避免用户误以为是在关闭配置面板。

后端新增：

- `POST /config/notion/create-database`
- 只允许 Chrome extension origin 修改本地配置。
- 成功创建 Notion database 后写入本机配置，并启用 Notion 备份。

## 为什么改

手动让用户创建一套字段完全一致的 Notion database 太脆弱，也不符合普通用户首次配置路径。Notion 在当前产品里只是外部备份和旧数据导入通道，不应该成为核心功能的安装门槛。

Popup 是用户最自然的状态确认和配置入口，因此 AI 服务、Notion 备份状态和切换动作都放在这里，而不是让用户理解端口、worker、环境变量或后端脚本。

## 产品共识

- 本地 SQLite 是主数据源；Notion 是可选云端备份，不是必需账号。
- 每台电脑的记忆只来自这台电脑的本地数据、私有上下文文件、当前页面内容，或用户明确授权的 connector。
- 开源仓库不能携带 maintainer / user0 / 真实用户的默认记忆。
- 外部知识可以用于解释和研究，但不能自动冒充用户自己的项目、关注点或历史记忆。

## 用户如何验收

1. `git pull` 到最新 `main`。
2. 在 `chrome://extensions` 刷新 Margin 插件。
3. 刷新正在阅读的网页。
4. 打开 Popup，点 `AI 服务` 的 `切换`，应看到配置面板和 `收起`。
5. 点 `云端备份` 的 `配置`，应看到：
   - `打开 Notion integrations 页面`
   - `放数据库的 Notion 页面链接`
   - `自动创建数据库`
   - `暂停云端备份`
6. 在 Notion 创建 integration，复制 Secret；新建一个空白页面，并在 Share / Connections 里授权该 integration。
7. 把 Secret 和页面链接填入 Popup，点 `自动创建数据库`。

预期：

- Popup 显示 `Notion 数据库已创建`。
- 配置面板收起。
- 云端备份状态显示 `Notion 已开启`。
- 用户不需要手动创建字段完全一致的 database。

## 已验证

- `node --check src/popup/popup.js`
- `PYTHONPYCACHEPREFIX=/tmp/kb-pycache python3 -m py_compile backend/agent_api.py backend/worker.py backend/llm_client.py`
- Popup 自动化检查：AI 面板可收起；Notion 自动创建按钮会调用后端，成功后写回 database id 并收起面板。
- `scripts/kb-health`：重启后全部正常。
- `git ls-remote origin refs/heads/main`：确认远端 `main` 为 `5db1433`。

## 适用范围

这份记录适用于 2026-05-12 `main` 上的 Popup 配置入口和 Notion 自动建库实现。

## 可能过时的地方

- Popup 文案、字段名和配置入口可能随 UI 迭代变化。
- Notion API 的 database 创建能力、权限模型或 integration 页面入口可能随 Notion 平台变化。
- 后续如果 Notion 从备份通道变成正式云同步通道，需要重新评估数据主从关系、冲突处理和隐私说明。
