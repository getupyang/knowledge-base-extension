# 2026-05-14 · Page Memory Digests 实验设计

- 记录时间：2026-05-14
- 分支：`feat/page-summary-eval`
- 关联 commit：待提交
- 当前范围：只做文档、schema、合成 fixture、本地离线评测 harness；不接生产 `Context Loader`、不改 notebook runtime。

## 结论

mem-ai 不缺“把网页压短”的普通摘要，缺的是一个可审计的网页记忆理解层：后续 AI 能知道页面是什么、用户和页面发生过什么、这些页面是否在共同指向某个 active question / project / theme，同时不能把弱证据说成用户观点。

成功标准不是摘要好看，而是后续回复更 user-specific，并且 `wrong-memory` / `overclaim` 不上升。

## 当前事实

本地只做统计体检，不输出真实页面内容：

- `page_cache`：80 页
- `summary` 有效数量：0
- 全文平均长度：约 14k 字符
- 超过 20k 字符：17 页

所以当前问题不是“摘要略差”，而是 exposure memory 基本只有全文和关键词召回，缺一层可审计的页面理解对象。

## 调研判断

### Input Cleaning Layer

这些工具解决“网页进入系统前如何变干净”，不是记忆理解层本身。

- [Trafilatura](https://trafilatura.readthedocs.io/en/stable/index.html)：官方定位是网页文本 gathering / extraction，支持 main text、metadata、comments、多种输出格式，并强调在去噪 precision 与保留 recall 之间平衡。适合 server/backfill 清洗。
- [Mozilla Readability](https://github.com/mozilla/readability)：Firefox Reader View 的 standalone 版本，`parse()` 后有 `title`、`content`、`textContent`、`length`、`excerpt`、`byline`、`siteName` 等。适合 extension/browser 侧提取正文。
- [Jina Reader](https://jina.ai/reader/)：把 URL 转成 LLM-friendly input，适合 opt-in 外部辅助；但它是云 URL 处理，不符合 mem-ai local-first 默认。

产品取舍：可以把它们作为 extractor 选项记录在 provenance 里，但不能把它们当作三版摘要实验。

### Summarization Mechanics

这些框架解决“长文如何被模型处理”，不是产品答案。

- [LlamaIndex response synthesizers](https://developers.llamaindex.ai/python/framework/module_guides/querying/response_synthesizers/)：`refine`、`compact`、`tree_summarize`、`simple_summarize` 覆盖不同长文处理方式；但如果不保留 chunk/span provenance，仍会生成不可审计摘要。
- [sumy](https://pypi.org/project/sumy/)：本地 extractive summarization 工具，支持 LSA / LexRank / TextRank 等。适合便宜 smoke 或对照，但不理解用户行为，也不能判断 `seen` / `highlighted` / `commented` 证据等级。

产品取舍：可以复用长文处理方式；三版实验必须围绕 evidence、provenance、user-specific prediction。

### Memory Product Lessons

- [Mem0 Custom Instructions](https://docs.mem0.ai/open-source/features/custom-instructions)：事实抽取需要按产品用途定制；prompt 太宽会进杂质；需要 negative examples、versioning、regression。
- [Zep entity summaries](https://help.getzep.com/entities)：facts 与 summaries 分层，facts 是 granular、temporal，summaries 是 entity-level narrative，两者都进入 context。
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：schema 能提高结构可靠性，但仍要配 eval；JSON 合法不等于事实正确。
- [Anthropic Clio privacy notes](https://privacy.claude.com/en/articles/10812588-how-does-clio-analyze-usage-patterns-while-protecting-user-data)：高层模式发现要有聚合、阈值、校验。mem-ai 是单用户本地，不照搬匿名聚合，但要吸收“高层 insight 必须有证据边界”的原则。

产品取舍：自研 schema + provenance + eval，比直接引入普通 summarizer 更符合 mem-ai。

## 实验矩阵

### Experiment A · Source Digest

决策问题：只做“页面是什么”的 source-level digest，是否已经能显著提升召回/引用质量，而不用触碰用户兴趣推断？

对象：单条 `page_cache`。

输出：`source_digest_json`。

不允许字段：`user_interest`、`user_stance`、`user_project`。

核心字段：

- `source_id`
- `page_url_hash`
- `page_title`
- `content_hash`
- `cleaned_content_hash`
- `normalized_text_hash`
- `cleaned_text_chars`
- `extractor`
- `digest_version`
- `document_type`
- `sections`
- `claims`
- `entities`
- `retrieval_hints`
- `limitations`

通过门槛：

- Grounding：每条 `claim` / `retrieval_hints` 必须能回链到 section/span/hash，抽查 10 页准确率 >= 90%。
- Retrieval：对冻结 query，Source Digest 检索相对当前 `full_text` keyword 的 MRR / Recall@3 有提升；至少不降低 same-page exact query。
- Token：Context Loader 引用 Source Digest 时，平均上下文字符下降 >= 60%。

保留不确定性：如果 A 已经解决大部分召回，不急着把 V2 接入生产。

### Experiment B · Exposure Digest

决策问题：加入用户行为证据后，能否更好预测用户 attention / gist，同时 severe overclaim 为 0？

对象：`page_cache` + `page_exposure_events` + selected text / comment / replies。

输出：`exposure_digest_json`，按 exposure event/thread 生成，不覆盖 Source Digest。

核心字段：

- `exposure_id`
- `page_cache_id`
- `evidence_level`: `seen` | `highlighted` | `commented` | `asked` | `corrected`
- `observed_behavior`
- `user_authored_text`
- `selected_text_refs`
- `inferred_attention`
- `possible_interest`
- `stance_if_any`
- `active_question_candidates`
- `must_not_infer`
- `temporal_boundary`
- `promotion_rules`

证据规则：

- `seen`：只能表示系统有证据用户接触过页面，不得写“用户认为/喜欢/正在做”。
- `highlighted`：中高兴趣信号，可推断 attention，不得推断 stance。
- `commented` / `asked`：用户主动表达，可推断 candidate interest 与 stance，但必须带 evidence refs。
- `corrected`：纠正优先级高于早期摘要或推断，后续 digest 必须保留 correction。

通过门槛：

- Evidence-boundary：`seen-only` severe overclaim = 0；`highlighted` 不得推断 stance；`commented` 才允许 `stance_if_any`。
- Personal Gist：在 Gist / Attention / Objection / Next Move rubric 上，Exposure Digest 相比 Source Digest 在 Attention 或 Objection 至少 +1 平均分，且 wrong-scope memory severe = 0。
- Correction handling：有 `corrected` 时，digest 必须保留 correction 优先级，不被早期页面摘要覆盖。

保留不确定性：B 能否稳定带来 user-specific lift，而不是让系统开始脑补。

### Experiment C · Memory Growth Signals

决策问题：跨页面合成是否真的改善后续 AI 回复，而不是生成漂亮但用户不信任的项目总结？

对象：近期 Source Digest + Exposure Digest。第一阶段只做 local-only batch，不直接改 production context loader。

输出：`memory_growth_signals_json`。不要叫 `summary`。

核心字段：

- `temporal_boundary`
- `active_questions`
- `project_clues`
- `theme_heat`
- `contradictions_or_shifts`
- `should_surface_to_user`
- `display_rule`

通过门槛：

- Trust：页面展示必须先证据链 / recency / strength，再结论；不能只显示“当前项目”。
- Accuracy：人工标注的 active question / theme 覆盖 >= 70%，但 unsupported project/theme severe = 0。
- Usefulness：用 C 作为 context 的后续回复，在 Personal Gist 的 Gist / Next Move 至少 +1，且 overclaim 不高于 B。
- Premature-closure：评审中“像被归档/被定性”的失败率 < 20%。

保留不确定性：C 是否值得进 notebook/product，而不是只做后台研究信号。

## Provenance 规格

所有 span 都基于 `normalized_text`，不是 raw HTML，也不是渲染 DOM。

规范：

- `content_hash`：`sha256(raw_content_utf8)` 的前 16 位十六进制。raw content 可以是当前 content script 上报的全文或 extractor 原始输入。
- `cleaned_content_hash`：`sha256(cleaned_text_utf8)` 前 16 位。cleaned text 是 extractor 去噪后的正文。
- `normalized_text_hash`：`sha256(normalized_text_utf8)` 前 16 位。normalized text 是 collapse whitespace 后用于 span 的稳定文本。
- `source_span.start/end`：Python slice 风格，`start` inclusive、`end` exclusive，基于 `normalized_text` 字符偏移。
- `quote_hash`：`sha256(normalized_text[start:end].encode("utf-8"))` 前 16 位。
- `section_id`：同一个 digest 内稳定即可；跨 extractor 重跑不保证稳定。

迁移/失效规则：

- 如果 `normalized_text_hash` 未变，旧 span 继续有效。
- 如果 `normalized_text_hash` 变了但 `quote_hash` 可在新 normalized text 中唯一命中，可迁移 span。
- 如果 `quote_hash` 无法唯一命中，相关 claim / hint 标记为 `stale_provenance`，不能进入生产 context。

## 评测集

不提交真实页面、真实批注、真实 URL、真实 selected text。真实 eval 只放 `.local/`。

- Retrieval set：从本地 `page_cache` 人工选 15-20 个 query，标 gold page ids 和 gold evidence spans。query 必须在看 digest 前冻结，或由独立 reviewer 标注，避免被 `retrieval_hints` 反向污染。
- Evidence-boundary set：`seen` / `highlighted` / `commented` / `asked` / `corrected` 各 5-10 个。真实样本不足时用 synthetic fixture 补边界，不伪装成真实效果。
- Personal gist set：复用 existing benchmark rubric：Gist、Attention、Objection、Next Move；比较 Source Digest、Exposure Digest、raw selected/comment context，不只和 `no_memory` 比。
- Theme synthesis set：按近期真实 digest，人工标 active questions / projects / themes + must_not_surface items。

## Failure 定义

- `severe_overclaim`：把 `seen` / `highlighted` 等弱证据写成用户观点、偏好、认同、正在做的项目，或在没有用户主动表达时推断 stance。
- `wrong_scope_memory`：使用不属于当前用户、当前项目、当前主题、当前 evidence level 的记忆。
- `unsupported_theme_or_project`：没有 evidence_refs，或 evidence 只来自 seen-only，却输出 confirmed project / active theme。
- `premature_closure`：把 recent clue 包装成稳定归档结论，让用户感觉系统已经替他定性。

处理规则：

- 任一 severe failure 默认 blocking，不能接入 production。
- 允许修 prompt/schema 后重跑，但报告必须同时保留失败版本，不只展示修复后结果。
- 平均分不能抵消 severe failure。

## 实现顺序

1. 只写 docs / schemas / synthetic fixtures / local eval harness。
2. Source Digest backfill/eval：先证明 A 对召回和 grounding 有价值。
3. Exposure Digest eval：只有 severe overclaim = 0 才允许考虑接 Context Loader。
4. Memory Growth Signals 只做 local-only batch 和 notebook mock；必须用 rubric 评估“证据先行、是否过早定性、是否可纠正”。
5. 最后再决定是否把 B/C 接入生产 context pack。

## 适用范围与过时风险

这份文档适用于 2026-05-14 的 runtime 状态：`page_cache` 已有全文缓存，但 `summary` 基本为空，Context Loader 可召回 exposure pages。后续如果 schema、Context Loader、page cleaning、或 notebook 信息架构变化，本设计需要重新评审。
