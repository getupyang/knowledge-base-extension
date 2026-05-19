# Regression Gate

提交前固定命令：

```bash
scripts/kb-regression
# or
npm run regression
```

这个命令是提交门禁，不是展示页。任一必需项失败都会返回非 0。

## 2026-05-19 · Support Report Debug Packet Note

- 记录时间：2026-05-19 15:10 Asia/Shanghai
- 关联 commit：`f72c000`
- 分支：`codex/debug-report-packet`
- 改了什么：新增本机私有 `llm_request_snapshots` 和用户明确同意后提交的 `support_reports`；点踩时发送“反馈 + 诊断包”，失败/超时/仍在处理的 AI 回复展示“报告问题”入口。
- 为什么改：默认 telemetry/ledger 能定位慢和失败，但无法还原具体现场；直接默认上传评论、划线、URL、AI 回复会伤害熟人内测的隐私信任。V0 默认只上传诊断元数据，勾选后才附带正文或模型 I/O。
- 用户如何验收：刷新 Chrome extension 并刷新目标网页；对正常 AI 回复点“踩”，应看到诊断包预览和四个可选附件；对失败或“AI 仍在处理中”的回复，应看到“报告问题”按钮；不勾选附件时，问题报告只包含版本、事件时间线、ledger、snapshot hash/长度等诊断数据。
- 已验证：`python3 -m py_compile backend/agent_api.py`、`node --check src/content/index.js`、`scripts/kb-regression --skip-live --skip-browser`、临时 `KB_DATA_DIR=/private/tmp/kb-support-report-smoke` 的 `/debug/problem-reports/preview` 与提交 smoke。2026-05-19 15:27 已部署 margin-cloud 依赖并用本 worktree 重启本地 8765/8766/worker，`scripts/kb-health` 所有核心项为 ✓，仍有既有 `margin_cloud_sync` warning；本地 `/debug/problem-reports/preview` 对最新 comment 返回 200 且未写入数据。完整 `scripts/kb-regression` 未作为通过项：live health 因既有 `margin_cloud_sync` warn 返回非 0，browser smoke 因该 worktree 未安装 Playwright 失败。
- 适用范围：`backend/agent_api.py`、`src/content/index.js`，并依赖 margin-cloud 的 `/api/support-reports` 和 `margin_support_reports` 表用于云端查看。
- 可能过时的地方：问题报告字段会随 admin UI、LLM ledger、request snapshot schema 演进；`include_model_io` 属于高敏感授权附件，不应成为默认选项。

## 2026-05-19 · Support Report Copy Tuning

- 记录时间：2026-05-19 15:58 Asia/Shanghai
- 关联 commit：`52878ed`
- 分支：`codex/debug-report-packet`
- 改了什么：问题报告面板默认勾选四类授权附件；把 checkbox 改成更清晰的确认项；移除“刷新预览”按钮；把 `ledger`、`snapshot`、计数等开发者文案改成用户能理解的“会发送哪些材料、为什么有助于排查”。
- 为什么改：用户看到第一版后反馈多选框太丑、默认不勾选不符合熟人内测排查诉求，并且内部字段名不可理解。
- 用户如何验收：重新加载 Chrome extension 并刷新目标网页；点踩或报告问题时，应看到四项默认勾选的发送内容确认，不再出现“刷新预览”按钮，也不再显示 `snapshot` / `LLM` 计数行。
- 已验证：`node --check src/content/index.js`、`git diff --check`、`scripts/kb-regression --skip-live --skip-browser`。
- 适用范围：仅影响 content script 的问题报告面板呈现和默认附件勾选，不改变后端 report schema。
- 可能过时的地方：如果后续把问题报告改成独立 modal 或更细的隐私 preset，需要重新评估默认勾选策略，特别是 `include_model_io`。

## 2026-05-19 · Support Report Sync Status

- 记录时间：2026-05-19 15:52 Asia/Shanghai
- 关联 commit：`65d4495`
- 分支：`codex/debug-report-packet`
- 改了什么：问题报告同步到云端成功后，本地 `support_reports.status` 从 `local_only` 更新为 `synced`，避免本地排查时误以为报告没有回传。
- 为什么改：用户提交报告后需要确认“回传数据是否符合预期”；之前 `synced_to_cloud_at` 已写入但 status 文案没同步，容易造成误判。
- 用户如何验收：提交问题报告后查询本地 `support_reports`，应看到 `synced_to_cloud_at` 有时间且 `status='synced'`；云端 `/admin/reports` 能看到同一个 `report_id`。
- 已验证：`python3 -m py_compile backend/agent_api.py`、`scripts/kb-regression --skip-live --skip-browser`、重启服务后 `scripts/kb-health` 核心项为 ✓。
- 适用范围：仅影响本地 support report 同步状态，不改变上传内容或云端 schema。
- 可能过时的地方：如果未来引入 retry/error 状态机，需要把 `status` 从简单字符串升级成更完整的同步状态。

## 2026-05-19 · Positive Example Feedback Packet

- 记录时间：2026-05-19 16:35 Asia/Shanghai
- 关联 commit：`7f842c6`
- 分支：`codex/positive-feedback-packet`
- 改了什么：点赞仍先记录轻量 `rating=up` 反馈；随后单独询问用户是否把这次作为“好例子”发给开发者。用户确认后复用诊断包发送确认，保存为 `rating=positive_example`。
- 为什么改：正反馈也有产品迭代价值，但不应让每次点赞默认上传 transcript；采用 Claude Code 类似的“两步”做法，先 rating-only，再单独征求 transcript/上下文授权。
- 用户如何验收：重新加载 Chrome extension 并刷新网页；对正常 AI 回复点“赞”，应先看到“已记录”，随后出现“愿意把这次作为一个好例子发给开发者吗？”；点“发送好例子”后出现可取消勾选的发送确认，提交后云端 reports 中 `rating` 应为 `positive_example`。
- 已验证：`node --check src/content/index.js`、`git diff --check`、`scripts/kb-regression --skip-live --skip-browser`。
- 适用范围：仅影响 content script 中赞后的可选正样本上传流程；不改变踩和报告问题的路径，也不改变云端 schema。
- 可能过时的地方：如果未来把 `/admin/reports` 改名为更通用的 feedback packets，需要同步调整本节验收入口。

## 2026-05-18 · Notebook Privacy Audit Note

- 记录时间：2026-05-18 21:30 Asia/Shanghai
- 关联 commits：`fab35bc`、`05bfc8e`、`cede099`、`27c45f4`
- 改了什么：移除 Notebook 中 maintainer-specific private-memory fallback 和 hardcoded Better Question clusters；把用户可见 provider/runtime 文案收敛为 `Margin`；区分真正生成和本地 evidence refresh；修正 stale rules 状态；移除全局点阵背景。
- 为什么改：Notebook 曾在无本机证据或弱证据场景下 fallback 到开发者私有样例，存在跨用户记忆泄漏风险，必须作为隐私 bug 修复。
- 用户如何验收：更新到包含上述 commits 的 `main`，重新加载 Chrome extension 并刷新 Notebook；确认 `你 & 项目`、`思考地图`、`养成的习惯`、`最近你在想的事` 不再出现 maintainer-specific 私有 topic 或旧样例内容；确认 `当前项目` 是 `刷新线索`，不是伪装成 LLM 重新生成；确认前端不出现 explicit provider/runtime 名称。
- 已验证：`node --check src/notebook/app.js`、`python3 -m py_compile backend/agent_api.py scripts/kb-regression`、`git diff --check`、`scripts/kb-regression --skip-live --skip-browser`。
- 适用范围：Notebook 前端、thought-map/memory-map 后端推断、rules/skills distillation、Notebook UI 文案与静态回归门禁。
- 可能过时的地方：`当前项目`和`思考地图`目前仍是本地 evidence map，不是 LLM-generated snapshot；如果后续新增 generated project snapshot，需要同步更新刷新语义和本节说明。

## 1. Static Checks

- Python syntax: `backend/llm_client.py`, `backend/agent_api.py`, `backend/worker.py`, `backend/server.py`, `scripts/kb-health`, `scripts/kb-regression`.
- JavaScript syntax: extension content/background/notebook/popup/sidepanel/collector scripts and legacy test scripts.
- HTML inline script syntax: `src/debug/sqlite-console.html`.
- JSON syntax: `package.json`, `manifest.json`.
- Contract scan:
  - Memory Intake Ledger exists.
  - Memory Input Events exists.
  - Exposure Memory exists.
  - Context Packs exists.
  - Memory Growth result tables exist.
  - Local-first capture paths exist, with legacy Notion routes kept for compatibility.
  - Follow-up flow records to backend and then triggers AI by default.
  - Debug Console exposes events, context packs, field notes, and acceptance state.
  - Notebook and thought-map code does not ship maintainer-specific private-memory topics or hardcoded Better Question clusters.
  - Notebook user-facing copy hides provider/runtime names behind Margin wording.

## 2. Hermetic API/DB Regression

Writes run only in a temporary `KB_DATA_DIR`. The user's real DB and optional Notion backup are not written.

- Create comment with `no_agent=true`.
- Verify `comments` row.
- Verify `page_cache` stores page full text.
- Verify `page_exposure_events` records `commented` evidence.
- Verify `memory_intake_ledger` is created and growth is enqueued.
- Verify `memory_input_events.comment_created` is created.
- Verify `jobs.memory_growth_for_comment` contains `comment_id`, `event_id`, and `trigger_reason`.
- Add a user follow-up.
- Verify `replies.author=user`.
- Verify `memory_input_events.user_followup`.
- Verify follow-up growth job enqueue and event-level dedupe.
- Record weak `seen` exposure through `/exposures/seen`.
- Verify `p#` exposure pages are retrievable by Context Loader.
- Verify `/notebook/page-cache/{id}` exposes page metadata and exposure events.
- Patch the comment conversation snapshot.
- Verify patch does not create duplicate memory events.
- Insert deterministic agent reply/context-pack/growth fixtures in the temp DB.
- Verify Debug Detail exposes:
  - memory events,
  - context packs,
  - exposure page ids and exposure refs,
  - ledger,
  - jobs,
  - comment interpretation,
  - event interpretation,
  - rule candidates,
  - active questions,
  - theme signals,
  - project signals,
  - profile signals.

Boundary cases currently enforced:

- Empty comment returns `400`.
- Empty follow-up returns `400`.
- Follow-up to missing comment returns `404`.
- Empty conversation patch returns `400`.
- Patch to missing comment returns `404`.
- Unknown job kind returns `400`.
- Duplicate event growth job is deduped instead of queued twice.
- Empty seen exposure returns `400`.

## 3. Live Read-Only Smoke

The live section reads the real running system but does not write user memory.

- Runs `scripts/kb-health --json`.
- Fails on any `fail` or `warn`.
- Reads `/debug/comments?limit=1`.
- Reads `/debug/comments/{id}` for either:
  - `--comment-id`,
  - `KB_REGRESSION_COMMENT_ID`,
  - or the latest live comment.
- Verifies Debug Detail response shape includes ledger, events, context packs, growth artifacts, jobs, and DB path.
- Exposure refs in context packs are treated as weak `p#` evidence, separate from active `c#` user comments.

## 4. Browser Smoke

- Opens `src/debug/sqlite-console.html` in headless Playwright.
- Uses live localhost API read-only.
- If a live comment id is available, verifies the page renders:
  - acceptance state,
  - Memory Events,
  - Context Packs,
  - field notes,
  - copy acceptance summary button.

Optional flags:

```bash
scripts/kb-regression --comment-id 204
scripts/kb-regression --skip-browser
scripts/kb-regression --skip-live
scripts/kb-regression --debug-url "http://localhost:8765/topics/external/2026-05-06-mem-ai/sqlite-debug-console.html?comment=204"
```

## Submission Rule

For backend changes:

1. Before editing backend code, run `kb-health`.
2. After editing and restarting services, run `kb-health` again.
3. Before committing, run `scripts/kb-regression`.

## Not Yet Covered

These are intentionally not in the default gate yet because they would either call external services or need a curated eval set:

- Live LLM answer quality golden tests.
- Full optional Notion backup regression.
- Cross-browser packaged-extension E2E with real Chrome UI.
- Exposure-memory retrieval benchmark over stored full-text articles.
- Longitudinal memory-quality benchmark across weeks of user behavior.
