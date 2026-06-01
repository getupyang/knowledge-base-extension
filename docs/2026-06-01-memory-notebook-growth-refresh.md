## 2026-06-01 · Memory Notebook Growth Refresh

- 记录时间：2026-06-01 15:50 Asia/Shanghai
- 关联 commit：`75ddae1` (`fix: refresh notebook habits from growth candidates`)
- 分支：`codex/kbe-memory-privacy-rules`，目标发布到 `main`
- 涉及文件：`backend/agent_api.py`、`backend/worker.py`、`src/notebook/app.js`、`scripts/kb-regression`、`AGENTS.md`
- 改了什么：`notebook/skills` 和 `notebook/rules/curated` 优先使用本机 SQLite 中的 `rule_candidates` 作为“养成的习惯”提炼来源；私有上下文按本机数据目录来源判断可信，不再按内容关键词硬过滤；notebook 前端显示新候选数量和 stale 状态。
- 为什么改：开发者也可能是当前产品用户，不能因为本机记忆包含开发者项目词而被过滤；但开发者记忆也不能进入 Git、fixtures、默认样例或 fallback。正确边界是数据来源和打包边界，而不是内容关键词。
- 用户如何验收：重启后端到包含本 commit 的代码，刷新 Chrome extension 和 notebook 页面；打开“记忆笔记本 / 养成的习惯”，当本机有新的 `rule_candidates` 时应看到新反馈待整理；点击“重新提炼”后，应根据真实结果显示“已整理成新版”或“新证据不足，已保留上一版”。
- 已验证：
  - `python3 -m py_compile backend/agent_api.py backend/worker.py scripts/kb-regression`
  - `node --check src/notebook/app.js`
  - `git diff --check`
  - `scripts/kb-regression --skip-live --skip-browser`
  - `scripts/kb-health`
- 适用范围：本记录适用于 2026-06-01 的 local-first memory growth / notebook working skills 实现。真实用户记忆仍保存在各自本机 `KB_DATA_DIR`，默认路径为 `~/.knowledge-base-extension`。
- 可能过时的地方：如果后续 `working_skills` promotion 机制、DB schema、notebook IA、worker job 调度阈值或运行端口发生变化，本记录中的验收步骤需要同步更新。
- 隐私边界：本文档和测试只描述结构与合成断言，不包含真实用户评论、真实项目记忆、URL、截图、token 或 SQLite 内容。
