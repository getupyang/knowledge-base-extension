#!/usr/bin/env node
// popup DOM 契约：popup.js 里 $("id") 引用的每个元素必须存在于 index.html，
// 防止改 UI 时出现 null.addEventListener 之类的运行时断裂。
"use strict";

const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "src", "popup", "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "..", "src", "popup", "popup.js"), "utf8");

const htmlIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const referenced = new Set([...js.matchAll(/\$\("([^"]+)"\)/g)].map((m) => m[1]));

const missing = [...referenced].filter((id) => !htmlIds.has(id));
if (missing.length) {
  console.error("popup.js 引用了 index.html 中不存在的元素 ID：", missing.join(", "));
  process.exit(1);
}

// 引用了外部脚本的完整性：index.html 里的 <script src> 文件必须存在
for (const m of html.matchAll(/<script\s+src="([^"]+)"/g)) {
  const p = path.join(__dirname, "..", "src", "popup", m[1]);
  if (!fs.existsSync(p)) {
    console.error(`index.html 引用的脚本不存在：${m[1]}`);
    process.exit(1);
  }
}

console.log(`test-popup-dom: ${referenced.size} 个 ID 引用全部命中`);
