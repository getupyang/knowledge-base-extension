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

## 私有记忆与代码仓库边界

这个仓库可以开源运行时代码，但不能携带任何真实用户的私有记忆。开发时按下面边界处理：

必须留在本机、默认不进 Git：

- SQLite 数据库、Notion 缓存、评论、AI 回复、划线原文、highlight anchors、context_packs、jobs、memory_events。
- `backend/user_profile.md`、`backend/project_context.md`、`backend/learned_rules.json`、thinking snapshots、任何从真实用户行为蒸馏出的记忆文件。
- gold set、真实 replay case、朋友/用户 DB 导出、含真实评论或页面内容的截图。
- API key、Notion token/database id、本地 provider/agent 私有配置。

可以进入 Git：

- 源代码、schema、migration、prompt 模板、validator、UI、回归脚本。
- 假数据 fixtures 和脱敏样例。
- 机制文档，但不能把第零号用户或任何真实用户的项目/评论/记忆写成默认内容。

个性化回复和 notebook 生成规则：

- 只能从当前设备的本地 SQLite、当前设备私有上下文文件、当前页面内容、或用户显式授权的 connector 装载私有记忆。
- 不允许在空库、导入失败、Notion 不通、SQLite 为空时 fallback 到 maintainer / user0 / 仓库默认记忆。
- 公开外部知识可以用于研究、解释和补充背景；但外部资料不能自动变成“用户自己的项目/关注点/记忆”，除非本机证据明确支持。
- “对你项目的关联”“你 & 项目”“思考地图”等结论必须可追溯到本机证据；没有证据时展示空状态、低置信候选或外部背景，不要硬编。

提交前必须检查 `.gitignore` 和 `git status --short`。只 stage 本次相关文件；发现私有数据进入 Git 时，先停下并清理，不继续叠加提交。

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

## GitHub 提交必须补短文档

每次有代码、配置、产品体验、部署流程或回归脚本变更推到 GitHub，都必须补一份短文档或更新现有文档。不要只依赖 commit message、聊天记录或代码 diff。

文档要求：

- 文档必须写明记录日期，例如 `记录时间：2026-05-11`；如果是排查/发布过程，还要写明发生时间或验证时间。
- 文档必须关联 commit hash、分支、涉及文件、用户可见行为变化和验证命令。
- 文档必须说明适用范围和过时风险；凡是依赖当前 UI、当前数据库状态、当前运行进程、当前 provider、当前浏览器插件加载方式的内容，都要明确“这是当时状态，可能随实现变化过时”。
- 文档优先放在 `docs/`、`REGRESSION.md`、`README.md` 或对应功能的近邻文档；如果只是一次小修，可以在同一功能文档追加一个 dated note。
- 如果这次提交本身就是本地 AGENTS / 协作规则变更，可以只更新 AGENTS；如果是产品/运行时代码变更，不能只更新 AGENTS。
- commit 前检查文档里不要写入真实用户私有记忆、真实评论全文、API key、Notion token、SQLite 路径外的私有数据或不可公开截图。

推荐最小短文档结构：

```md
## YYYY-MM-DD · <改动标题>

- 记录时间：
- 关联 commit：
- 改了什么：
- 为什么改：
- 用户如何验收：
- 已验证：
- 适用范围：
- 可能过时的地方：
```

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

这个仓库可能同时被多个 Codex 窗口修改。开始任何 coding 前，必须先隔离工作区：

```bash
git fetch origin
git status --short --branch
git worktree add ../knowledge-base-extension-<task> -b feat/<task> origin/main
```

执行规则：

- 每个 Codex 窗口必须拥有自己的 git 分支；多人/多窗口或已有脏工作区时，必须使用独立 `git worktree`。
- 不允许直接在共享 `main` 工作区里开发。`main` 只用于同步、最终合并和用户明确要求的推送。
- 如果已经在 `main` 上准备改代码，先停下，新建 branch/worktree 后再动手。
- 明确本窗口负责哪些文件。
- 不碰未分配文件，不回滚他人改动。
- commit 前只 stage 本次相关文件。
- push 前确认当前分支、最近 commit、远端 main；默认推 feature branch，只有用户明确要求并完成检查后才推 main。
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
