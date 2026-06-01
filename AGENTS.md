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
- 用户安装、使用、排障需要的产品文档，例如 `README.md`、平台支持说明、公开 API / 配置说明。
- 必要的项目规则可以进入 `AGENTS.md`，但只写长期边界和协作约束，不写单次会话流水账。

不应进入 GitHub：

- 开发日志、会话复盘、handoff 过程稿、Codex 执行细节、内部验收流水账。
- 只给开发者看的微观过程记录，例如“本窗口做了什么、和用户达成了哪些临时共识、跑过哪些命令”的完整纪要。
- 真实用户反馈原文、真实截图、真实 replay/gold set、朋友电脑的安装排查记录，除非已经脱敏并明确转化为公开 issue、公开测试 fixture 或用户文档。
- 本地 Codex skill、个人 AGENTS、个人 workflow、机器路径相关的开发规则；这些应留在本机 Codex 配置或 `mem-ai` PMO 私有文档里。

个性化回复和 notebook 生成规则：

- 只能从当前设备的本地 SQLite、当前设备私有上下文文件、当前页面内容、或用户显式授权的 connector 装载私有记忆。
- 不允许在空库、导入失败、Notion 不通、SQLite 为空时 fallback 到 maintainer / user0 / 仓库默认记忆。
- 公开外部知识可以用于研究、解释和补充背景；但外部资料不能自动变成“用户自己的项目/关注点/记忆”，除非本机证据明确支持。
- “对你项目的关联”“你 & 项目”“思考地图”等结论必须可追溯到本机证据；没有证据时展示空状态、低置信候选或外部背景，不要硬编。

开发者作为用户的规则：

- 开发者本机也是一个正常用户环境。开发者应该能在自己的本机数据目录里看到真实记忆笔记本成长，不能因为内容提到项目名、产品名或开发者偏好就被误判为空库。
- 开发者记忆不能进入代码层。真实 `comments`、`selected_text`、`page_url`、`profile`、`project_context`、`learned_rules`、`working_skills`、`rule_candidates`、`thinking_summaries`、真实 replay 和截图，禁止写入源码、prompt 默认示例、公开 fixture、测试快照、README 示例或仓库默认 fallback。
- 隐私保护应基于来源和 provenance，而不是内容关键词。可信判断优先看数据是否来自当前 `KB_DATA_DIR`、当前 install/user、用户显式授权 connector、DB 记录来源、迁移记录和打包状态；不要因为文本里出现 `mem-ai`、`Margin`、项目名或类似开发者工作内容就直接过滤本机私有数据。
- 发布安全网仍必须存在：如果检测到私有上下文文件位于 Git 仓库、打包目录、公开 fixture、默认模板或无法证明属于当前 install，应拒绝作为用户记忆装载，并展示可解释的降级原因。

提交前必须检查 `.gitignore` 和 `git status --short`。只 stage 本次相关文件；发现私有数据进入 Git 时，先停下并清理，不继续叠加提交。

## 健康记忆生长与 Notebook 展示

运行时代码必须支持所有用户拥有独立、可成长、可审计的记忆笔记本：

- 记忆生长的主链路应是 `comments / replies -> memory_events -> rule_candidates / profile_signals / project_signals -> working_skills / snapshots -> notebook / context loader`。不要让 notebook 的核心模块长期依赖旧的静态 `learned_rules.json` 文件。
- `rule_candidates` 等候选信号不能直接冒充已生效习惯。进入 `working_skills` 前必须有 promotion 机制，记录证据、状态、置信度、scope、生成时间和来源。
- “重新提炼”必须真实说明发生了什么：生成新版、没有足够新证据、保留上一版、LLM 失败、隐私降级、还是 worker 未运行。禁止静默返回旧结果并给用户“已更新”的错觉。
- Notebook 每个记忆模块都要显示最小可验证元信息：版本时间、证据数量、新候选数量、当前是否 stale、以及用户能采取的下一步。
- 离线模拟和测试只能使用合成 fixture 或脱敏 fixture。测试不得从开发者真实 `KB_DATA_DIR` 读取内容，不得把开发者真实记忆写进断言、快照或 mock response。

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

## GitHub 发布边界

GitHub 是开源产品仓库，目标是让用户安装、理解、使用和信任产品；不是记录每次 Codex 会话和内部开发过程的地方。

推到 GitHub 前先判断这次内容属于哪一类：

应该推：

- 用户运行产品所需的代码、脚本、配置模板、schema、migration、prompt 模板、测试和回归脚本。
- 用户需要阅读的文档：安装、更新、平台支持、常见问题、配置说明、公开的排障指南。
- 对产品行为有长期约束的最小项目规则，例如本仓库的数据边界、隐私边界、多人协作边界。
- 脱敏后的 fixture、mock 数据、公开测试样例。

不应该推：

- 开发日志、窗口复盘、handoff 草稿、内部决策流水账。
- 只用于解释某次提交过程的记录，例如“我跑了哪些命令、用户如何确认、这次会话形成了哪些临时共识”。
- 本地 Codex skill、个人工作流、个人路径、机器状态、临时排查记录。
- 未脱敏的用户数据、真实评论、截图、Notion 内容、SQLite 导出、gold set、API key 或本地配置。

如果一次改动需要留痕：

- 面向用户的内容，写进 `README.md`、平台文档或公开 FAQ。
- 面向未来开发者的长期规则，只写进 `AGENTS.md` 的稳定边界，不写会话细节。
- 面向自己或团队复盘的开发日志，留在本地 Codex memory、`mem-ai` PMO 私有文档或其他不进入本开源仓库的位置。

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
