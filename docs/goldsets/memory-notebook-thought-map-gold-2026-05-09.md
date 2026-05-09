# Memory Notebook Thought Map Gold Set — 2026-05-09

Source commit: `8f7ecac fix: remove mem-ai labels from thought map`

This file freezes the quality target from the earlier high-quality Thought Map version.
It is a gold set for evaluation, not a runtime source of truth. Do not reintroduce these
author-specific topics into open-source runtime code.

## Why This Version Felt Better

The old version was wrong as a multi-user product because it used a hand-written topic
taxonomy. But it felt substantially better because it had editorial structure:

- Topics were named as high-level problems, not raw tokens.
- Each node had a role: mainline, merge, branch, sprout, occasional, archive/cooling.
- Each node had a human-readable interpretation of why the evidence mattered.
- It distinguished "interest", "project pressure", "background capability", and "temporary concept learning".
- It included "possible misread" language, so the product did not overclaim.
- Evidence cards explained the user's behavior, not only the keyword match.
- The overview sentence told a coherent story instead of listing extracted terms.

## Frozen Gold Topics

These are representative gold outputs from the old implementation. They are not allowed
as global defaults for other users.

| id | label | intended role | quality target |
| --- | --- | --- | --- |
| `knowledge-memory-systems` | 知识管理与个人记忆系统 | mainline | Persistent product/project line; connects annotations, memory, context loading, notebook UX. |
| `evaluation-methodology` | 评测与产品方法论 | merging | Explains that evaluation is becoming a product-method question, not just reading material. |
| `adoption-distribution` | 真实用户从哪里来，为什么会持续用 | branch | Captures GTM/adoption/retention anxiety as a product question. |
| `agent-workflow` | Agent 工作流与工程化协作 | branch | Captures how the user calibrates agents, regression, tools, and execution quality. |
| `social-impact-spatiotemporal` | AI 社会影响与空间数据研究 | sprout | Marks a newly captured interest without claiming it is a long-term profile. |
| `ai-video-gtm` | AI 视频 / 产品发布表达 | sprout | Differentiates product-distribution opportunity from random tool reading. |
| `reasoning-architecture` | AI 推理架构 / 加速机制 | occasional | Correctly labels one-off concept learning as not yet a stable line. |
| `early-value-narrative` | 第一天价值 / 创业方向探索 | cooling/archive | Correctly expresses an older background line whose direct heat has dropped. |

## Gold Behaviors To Preserve

1. Topic labels should be concepts, tensions, or workstreams, not surface strings.
   Bad: `评价一下`, `Claude`, `AI Builder Daily`, `Free screen recorder`.
   Good: `评测如何反过来指导产品判断`, `用户为什么会持续使用`, `Agent 工作流如何工程化`.

2. The map should say what the user is doing with the topic.
   Examples: concept learning, product validation, project execution, adoption anxiety,
   quality correction, method-building, follow-up research.

3. Lanes must be evidence-derived but semantically meaningful.
   `rising` should mean recent repeated evidence or stronger behavior, not a single recent token.
   `cooling` should mean older evidence with no recent continuation, not a hardcoded archive state.

4. The same evidence can support different layers.
   A paper title may be a topic.
   A repeated question may be an active question.
   A user-owned workstream may be a project.
   An external product is not automatically a user project.

5. Every high-level claim needs citations to local evidence.
   The system can be opinionated only after it can show the comments that justify the opinion.

6. Do not globalize the author's interests.
   Runtime output must be generated from the current user's local SQLite and private files.
   The old topics are allowed only as test expectations for this user's historical DB or as rubric examples.

## Current Regression Diagnosis

The current dynamic implementation dropped quality because it replaced a semantic layer with
shallow extraction:

- It extracts words or fragments from titles/comments, so action phrases become topics.
- It lacks a concept-naming step that turns multiple evidence items into a useful abstraction.
- It treats containers and sources as topics: daily reports, page titles, external product names.
- It does not distinguish user-owned projects from research objects or external comparisons.
- Its evidence ranking is mechanical, not editorial.
- Its interpretation text is generic and therefore less insightful than the previous per-topic text.
- It optimizes for "no cross-user hardcoding" but lost "good product taste".

## Product Direction

The desired implementation is not "hardcoded topics" and not "keyword extraction".
It should be:

1. Local evidence retrieval from this user's SQLite.
2. Candidate clustering by comments, selected text, page context, time, and behavior.
3. LLM or stronger local synthesis to name clusters as concepts.
4. A validator that rejects surface labels, source containers, pure action commands, and external objects as projects.
5. A stable JSON snapshot stored locally so the user sees a coherent map, not a newly improvised extraction every refresh.
6. Regression tests against this gold set:
   - no global author-specific topic leakage for a fresh user DB;
   - no action phrase as topic;
   - no page/source container as topic;
   - high-quality local DB output preserves concept labels, interpretive reads, possible-misread caveats, and evidence citations.

## Minimal Rubric

Score each generated Thought Map from 1 to 5:

- Concept quality: labels are meaningful abstractions.
- Personalization safety: no topic appears without local evidence.
- Evidence fidelity: each claim cites relevant comments.
- Layering: separates project, question, theme, and source object.
- Product usefulness: tells the user what to do or notice next.
- Humility: explicitly marks weak or new evidence instead of overclaiming.

Target before release: average >= 4, with no score below 3.
