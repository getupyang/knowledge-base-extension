# knowledge-base-extension — mem-ai Runtime

这个仓库是 mem-ai 的真实运行时代码仓库，不只是一个浏览器插件目录。这里包含：

- Chrome extension UI、content script、background、popup / side panel
- 本地后端 `Agent API`，默认端口 `8766`
- notebook 前端
- 本地 SQLite 数据和调试入口
- 健康检查、回归脚本、启动脚本

产品原则和长期项目判断在 `/Users/getupyang/mem-ai/AGENTS.md`。做产品、UI、记忆、growth、notebook 相关工作前，必须先读那份文件，不要只读本仓库旧的 `CLAUDE.md`。

## 当前 Source Of Truth

- 用户数据默认 local-first，每台电脑 / 每个安装独立。
- 当前运行时 SQLite：`/Users/getupyang/.knowledge-base-extension/comments.db`
- Git 仓库内的 DB 拷贝不是线上真实状态，除非用户明确指定。
- Notion 是 mirror / backup / 展示层，不是默认必需的 source of truth。
- 用户 memory 不应写进 GitHub 开源代码仓库；每个用户/设备应使用本地私有上下文。

如果遇到 Notion、SQLite、旧路径冲突，优先确认当前运行版本和数据路径，不要凭旧文档猜。

## 动手前健康检查

凡是改后端、worker、provider、DB、notebook 数据接口、Chrome extension 与后端交互前，先跑：

```bash
scripts/kb-health
```

规则：

- 有 `✗`：先处理或明确报告，不能假装服务正常。
- 有 `⚠`：告诉用户风险，尤其是 24h failure log、provider 状态、端口在听但 `/health` 无响应。
- 改完后如果涉及后端运行，必须重启服务并再次跑 `scripts/kb-health`。

## 分层回归策略

不要每次默认全量回归。按改动范围选择最小有效测试集：

```bash
# 每次都适合跑的 smoke
scripts/kb-health
git diff --check

# notebook / 前端语法
node --check src/notebook/app.js

# 项目回归
scripts/kb-regression
```

建议规则：

- 改 `src/content/index.js`：必须验证高亮、锚点、评论卡片、刷新恢复、AI pending 状态。
- 改 `src/notebook/`：必须验证 notebook 页面加载、tab/分段控件、timeline、未读状态、空状态。
- 改 `backend/llm_client.py`、provider、`choose_ai_service.sh`：必须验证 provider 选择、mock LLM、`kb-health` 的 LLM provider 项。
- 改 DB schema / migration：必须说明数据路径、备份/回滚方式、旧数据兼容。
- 改 prompt / agent 行为：优先用 mock / replay；真实模型调用只做少量 smoke，避免不必要 token 消耗。

最终回复要说明：本次跑了 smoke、局部回归还是 full regression；没跑 full 时说明原因和残余风险。

## Chrome Extension / Notebook 刷新规则

如果改了这些内容：

- `manifest.json`
- `src/content/*`
- `src/background/*`
- `src/popup/*`
- `src/sidepanel/*`
- `src/notebook/*`
- 注入页面的 CSS / JS / assets

final 必须提醒用户：

- 到 `chrome://extensions` 刷新插件。
- 刷新已经打开的目标网页，让 content script 重新注入。
- 如果改了后端，还要说明是否已重启服务，以及是否跑过 `scripts/kb-health`。

## UI / 产品规则

做任何用户可见 UI 前，必须先做信息架构判断：

- 用户第一眼要看懂什么？
- 页面主对象是什么？可操作对象是什么？详情对象是什么？
- 列表和详情是否在同一视觉上下文里？
- 独立模块是否用 tabs / 分段控件 / 清晰分区隔开？
- 新颜色是否表达状态、分组或强调？如果只是装饰，不要加。
- 新按钮是否沿用现有按钮样式？同一页面不要出现无理由不同样式的按钮。

页面结构优先表达关系，而不是展示实现细节。不要用一堆卡片把不相关层级堆在一起。

## 多 Codex 窗口协作

这个仓库可能同时被多个 Codex 窗口修改。开始 coding 前先跑：

```bash
git fetch
git status --short --branch
```

执行规则：

- 明确本窗口负责哪些文件。
- 不碰未分配文件，不回滚他人改动。
- commit 前只 stage 本次相关文件。
- push 前确认当前分支、最近 commit、远端 main。
- final 中列出仍存在的未提交差异，并说明是否与本次无关。

## 最终回复必须可验收

每次完成代码修改、配置修改、服务重启、发布到 GitHub、或影响用户体验的排查后，最终回复必须包含：

- 改了什么。
- 实际跑了哪些验证。
- 用户如何验收，预期现象是什么。
- 当前发布状态：本地修改 / 已提交未推送 / 已推到 GitHub。
- 分支和 commit hash。
- 是否需要刷新插件、刷新网页、重启后端。
- 是否有未提交差异，以及哪些不是本次带入。
