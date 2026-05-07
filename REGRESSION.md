# Regression Gate

提交前固定命令：

```bash
scripts/kb-regression
# or
npm run regression
```

这个命令是提交门禁，不是展示页。任一必需项失败都会返回非 0。

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
  - Notion paths still exist.
  - Follow-up flow records to backend and then triggers AI by default.
  - Debug Console exposes events, context packs, field notes, and acceptance state.

## 2. Hermetic API/DB Regression

Writes run only in a temporary `KB_DATA_DIR`. The user's real DB and Notion are not written.

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
- Full Notion write regression.
- Cross-browser packaged-extension E2E with real Chrome UI.
- Exposure-memory retrieval benchmark over stored full-text articles.
- Longitudinal memory-quality benchmark across weeks of user behavior.
