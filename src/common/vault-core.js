// Margin 本地批注库（vault）核心逻辑。
// 纯函数、零 chrome.* 依赖：background 通过 importScripts 引入，popup 通过 <script> 引入，
// node 测试通过 require 引入（scripts/test-vault-core.js）。
// vault 结构：{ version: 1, pages: { [pageUrl]: { title, updatedAt, comments: [], highlights: [] } } }
// 语义：content script 每次保存都发整页全量列表，vault 按 kind 整体覆盖该页对应列表，
// 因此删除也会随下一次保存自然同步；两类列表都为空时移除该页条目。
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.KBVaultCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const VERSION = 1;

  function emptyVault() {
    return { version: VERSION, pages: {} };
  }

  function normalizeVault(raw) {
    if (!raw || typeof raw !== "object" || !raw.pages || typeof raw.pages !== "object") {
      return emptyVault();
    }
    return { version: VERSION, pages: { ...raw.pages } };
  }

  function applyMirror(rawVault, mirror) {
    const vault = normalizeVault(rawVault);
    const { pageUrl, pageTitle, kind, items, mirroredAt } = mirror || {};
    if (!pageUrl || (kind !== "comments" && kind !== "highlights") || !Array.isArray(items)) {
      return vault;
    }
    const prev = vault.pages[pageUrl] || { title: "", updatedAt: "", comments: [], highlights: [] };
    const next = {
      title: pageTitle || prev.title || "",
      updatedAt: mirroredAt || prev.updatedAt || "",
      comments: kind === "comments" ? items : (Array.isArray(prev.comments) ? prev.comments : []),
      highlights: kind === "highlights" ? items : (Array.isArray(prev.highlights) ? prev.highlights : []),
    };
    if (!next.comments.length && !next.highlights.length) {
      delete vault.pages[pageUrl];
    } else {
      vault.pages[pageUrl] = next;
    }
    return vault;
  }

  function stats(rawVault) {
    const vault = normalizeVault(rawVault);
    let comments = 0;
    let highlights = 0;
    const urls = Object.keys(vault.pages);
    for (const url of urls) {
      const page = vault.pages[url] || {};
      comments += Array.isArray(page.comments) ? page.comments.length : 0;
      highlights += Array.isArray(page.highlights) ? page.highlights.length : 0;
    }
    return { pages: urls.length, comments, highlights };
  }

  function _sortedPages(vault) {
    return Object.entries(vault.pages)
      .map(([url, page]) => ({ url, ...page }))
      .sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
  }

  function toExportObject(rawVault, exportedAt) {
    const vault = normalizeVault(rawVault);
    return {
      source: "Margin",
      format: "margin-vault-export",
      version: VERSION,
      exportedAt: exportedAt || "",
      stats: stats(vault),
      pages: _sortedPages(vault),
    };
  }

  function _replyText(reply) {
    if (!reply || typeof reply !== "object") return "";
    return String(reply.content || reply.text || "").trim();
  }

  function toMarkdown(rawVault, exportedAt) {
    const vault = normalizeVault(rawVault);
    const s = stats(vault);
    const lines = [];
    lines.push("# Margin 批注导出");
    lines.push("");
    lines.push(`> 导出时间：${exportedAt || ""} · ${s.pages} 个页面 · ${s.comments} 条批注 · ${s.highlights} 条高亮`);
    for (const page of _sortedPages(vault)) {
      lines.push("");
      lines.push(`## ${page.title || page.url}`);
      lines.push("");
      lines.push(`<${page.url}>`);
      const comments = Array.isArray(page.comments) ? page.comments : [];
      const highlights = Array.isArray(page.highlights) ? page.highlights : [];
      for (const c of comments) {
        lines.push("");
        if (c.excerpt) lines.push(`> ${String(c.excerpt).replace(/\n+/g, " ")}`);
        if (c.text) lines.push(`- 想法：${String(c.text).trim()}`);
        if (c.createdAt) lines.push(`  - 时间：${c.createdAt}`);
        const replies = Array.isArray(c.replies) ? c.replies : [];
        for (const r of replies) {
          const text = _replyText(r);
          if (!text) continue;
          const who = r.author === "agent" ? "AI" : "我";
          lines.push(`  - ${who}：${text.replace(/\n+/g, " ")}`);
        }
      }
      if (highlights.length) {
        lines.push("");
        lines.push("高亮：");
        for (const h of highlights) {
          if (h.excerpt) lines.push(`- ${String(h.excerpt).replace(/\n+/g, " ")}`);
        }
      }
    }
    lines.push("");
    return lines.join("\n");
  }

  return { VERSION, emptyVault, normalizeVault, applyMirror, stats, toExportObject, toMarkdown };
});
