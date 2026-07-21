#!/usr/bin/env node
// vault-core 纯逻辑回归：node scripts/test-vault-core.js
"use strict";

const assert = require("assert");
const path = require("path");
const Vault = require(path.join(__dirname, "..", "src", "common", "vault-core.js"));

function mirror(vault, overrides) {
  return Vault.applyMirror(vault, {
    pageUrl: "https://example.com/a",
    pageTitle: "示例页",
    kind: "comments",
    items: [],
    mirroredAt: "2026-07-21T00:00:00Z",
    ...overrides,
  });
}

// 1. 空/坏输入 → 空 vault，不抛异常
for (const bad of [null, undefined, 0, "x", { pages: null }, []]) {
  const v = Vault.normalizeVault(bad);
  assert.deepStrictEqual(v.pages, {}, `normalizeVault(${JSON.stringify(bad)}) 应返回空 pages`);
}
assert.deepStrictEqual(Vault.applyMirror(null, null).pages, {}, "applyMirror 坏输入应安全返回");

// 2. 首次镜像评论
let v = mirror(null, { items: [{ id: 1, excerpt: "划线", text: "想法", replies: [] }] });
assert.strictEqual(v.pages["https://example.com/a"].comments.length, 1);
assert.strictEqual(v.pages["https://example.com/a"].title, "示例页");

// 3. 同页镜像高亮：不覆盖评论
v = mirror(v, { kind: "highlights", items: [{ id: 9, excerpt: "高亮句" }], mirroredAt: "2026-07-21T01:00:00Z" });
assert.strictEqual(v.pages["https://example.com/a"].comments.length, 1, "镜像高亮不应清掉评论");
assert.strictEqual(v.pages["https://example.com/a"].highlights.length, 1);
assert.strictEqual(v.pages["https://example.com/a"].updatedAt, "2026-07-21T01:00:00Z");

// 4. 整页覆盖语义：删除随全量列表同步
v = mirror(v, { items: [] });
assert.strictEqual(v.pages["https://example.com/a"].comments.length, 0, "空列表应覆盖旧评论");
v = mirror(v, { kind: "highlights", items: [] });
assert.strictEqual(v.pages["https://example.com/a"], undefined, "两类都为空时应移除页面条目");

// 5. 多页 stats
v = mirror(null, { items: [{ id: 1, text: "a" }, { id: 2, text: "b" }] });
v = mirror(v, { pageUrl: "https://example.com/b", pageTitle: "B", kind: "highlights", items: [{ id: 3, excerpt: "h" }] });
assert.deepStrictEqual(Vault.stats(v), { pages: 2, comments: 2, highlights: 1 });

// 6. 导出对象：按 updatedAt 倒序
v = mirror(v, { pageUrl: "https://example.com/b", kind: "highlights", items: [{ id: 3, excerpt: "h" }], mirroredAt: "2026-07-22T00:00:00Z" });
const exported = Vault.toExportObject(v, "2026-07-22T09:00:00Z");
assert.strictEqual(exported.pages[0].url, "https://example.com/b", "导出应按更新时间倒序");
assert.strictEqual(exported.stats.pages, 2);
assert.strictEqual(exported.exportedAt, "2026-07-22T09:00:00Z");

// 7. Markdown：含标题、划线、AI 回复；空 vault 不炸
v = mirror(null, {
  items: [{
    id: 1,
    excerpt: "被划的句子",
    text: "我的想法",
    createdAt: "2026-07-21T02:00:00Z",
    replies: [{ author: "agent", content: "AI 的回复\n第二行" }, { author: "user", text: "追问" }],
  }],
});
const md = Vault.toMarkdown(v, "2026-07-22T09:00:00Z");
assert.ok(md.includes("# Margin 批注导出"));
assert.ok(md.includes("> 被划的句子"));
assert.ok(md.includes("- 想法：我的想法"));
assert.ok(md.includes("AI：AI 的回复 第二行"), "AI 回复应换行压平");
assert.ok(md.includes("我：追问"));
assert.ok(Vault.toMarkdown(null, "").includes("0 个页面"));

// 8. 入参 vault 不被原地污染（pages 浅拷贝）
const original = mirror(null, { items: [{ id: 1 }] });
const after = mirror(original, { pageUrl: "https://example.com/c", items: [{ id: 2 }] });
assert.strictEqual(Object.keys(original.pages).length, 1, "applyMirror 不应原地修改入参的 pages 键集合");
assert.strictEqual(Object.keys(after.pages).length, 2);

console.log("test-vault-core: all assertions passed");
