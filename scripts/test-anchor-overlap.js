const { chromium } = require("playwright");
const http = require("http");
const os = require("os");
const path = require("path");
const fs = require("fs");

const EXTENSION_PATH = path.resolve(__dirname, "..");

const comments = [
  {
    id: 219,
    excerpt: "As we’ve discussed previously, we generally distinguish between tasks that involve automation (in which AI directly produces work with minimal user input) and augmentation (in which the user and AI collaborate to get things done). We further break automation down into directive and feedback loop interactions, where directive conversations involve the minimum of human interaction, and in feedback loop tasks, humans relay real-world outcomes back to the model. We also break augmentation down into learning (asking for information or explanations), task iteration (working with Claude collaboratively), and validation (asking for feedback).",
    text: "举几个例子吧，这些任务典型的都有哪些。",
  },
  {
    id: 216,
    excerpt: "还有什么能解释这种采用率差距呢？",
    text: "有没有可能是因为你很贵啊……",
  },
  {
    id: 217,
    excerpt: "O*NET 是美国政府数据库，用于对工作及其相关任务进行分类。",
    text: "中国有这类数据库吗？是不是也应该在中国做一个类似的分析。",
  },
  {
    id: 220,
    excerpt: "自动化任务（人工智能直接完成工作，用户输入极少）和增强任务 （用户与人工智能协作完成任务）。我们进一步将自动化任务细分为指令式交互和反馈循环交互。指令式交互涉及最少的人为干预，而反馈循环任务中，人类会将现实世界的结果反馈给模型。我们还将增强任务细分为学习 （请求信息或解释）、 任务迭代 （与 Claude 协作）和验证 （请求反馈）",
    text: "每个任务给我3个具体的例子。",
  },
];

function html() {
  return `<!doctype html>
    <meta charset="utf-8">
    <title>anchor overlap regression</title>
    <main>
      <article>
        <p>还有什么能解释这种采用率差距呢？</p>
        <p><strong>O*NET</strong> 是美国政府数据库，用于对工作及其相关任务进行分类。</p>
        <p>
          <span>As we’ve discussed previously, we generally distinguish between tasks that involve automation </span>
          <a href="#">(in which AI directly produces work with minimal user input)</a>
          <span> and augmentation (in which the user and AI collaborate to get things done). We further break automation down into directive and feedback loop interactions, where directive conversations involve the minimum of human interaction, and in feedback loop tasks, humans relay real-world outcomes back to the model. We also break augmentation down into learning (asking for information or explanations), task iteration (working with Claude collaboratively), and validation (asking for feedback).</span>
        </p>
        <p>
          <span>自动化任务（人工智能直接完成工作，用户输入极少）和增强任务 </span>
          <em>（用户与人工智能协作完成任务）</em>
          <span>。我们进一步将自动化任务细分为指令式交互和反馈循环交互。指令式交互涉及最少的人为干预，而反馈循环任务中，人类会将现实世界的结果反馈给模型。我们还将增强任务细分为学习 （请求信息或解释）、 任务迭代 （与 Claude 协作）和验证 （请求反馈）</span>
        </p>
      </article>
    </main>`;
}

function excerptId(excerpt) {
  let h = 0;
  const s = (excerpt || "").trim();
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return "ex" + (h >>> 0).toString(36);
}

function normalize(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function compact(value) {
  return String(value || "").replace(/\s+/g, "").trim();
}

async function serve() {
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(html());
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return server;
}

async function run() {
  const server = await serve();
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/article`;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kb-anchor-overlap-"));
  const storageKey = `kb_comments_${url}`;
  const storedComments = comments.map(c => ({
    ...c,
    createdAt: new Date().toISOString(),
    replies: [],
  }));

  const context = await chromium.launchPersistentContext(profile, {
    headless: false,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
    ],
    viewport: { width: 1280, height: 900 },
  });

  try {
    await context.addInitScript(({ key, value }) => {
      localStorage.setItem(key, JSON.stringify(value));
      window.__kbScrolls = [];
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function(opts) {
        window.__kbScrolls.push({
          id: this.id || "",
          text: (this.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
          opts,
        });
        this.dataset.kbScrolled = "1";
        if (original) return original.call(this, opts);
      };
    }, { key: storageKey, value: storedComments });

    const page = await context.newPage();
    page.on("console", msg => console.log(`[page:${msg.type()}] ${msg.text()}`));
    page.on("pageerror", err => console.log(`[pageerror] ${err.message}`));
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#kb-cmt-219", { state: "attached", timeout: 10000 });
    await page.waitForFunction(() => document.querySelectorAll("mark.kb-comment-highlight").length >= 4, null, { timeout: 10000 });

    for (const c of comments) {
      const id = excerptId(c.excerpt);
      const markTexts = await page.$$eval(`mark.kb-comment-highlight[data-excerpt-id="${id}"]`, nodes =>
        nodes.map(n => n.textContent || "")
      );
      if (!markTexts.length) {
        const debug = await page.evaluate(expected => ({
          bodyTextHasExcerpt: document.body.textContent.includes(expected),
          bodyTextHasNormalizedExcerpt: document.body.textContent.replace(/\s+/g, " ").trim().includes(expected.replace(/\s+/g, " ").trim()),
          markCount: document.querySelectorAll("mark.kb-comment-highlight").length,
          bodyStart: document.body.innerHTML.slice(0, 1500),
        }), c.excerpt);
        console.log("missing mark debug:", JSON.stringify(debug, null, 2));
        throw new Error(`missing mark for comment ${c.id}`);
      }
      const joined = normalize(markTexts.join(""));
      const expected = normalize(c.excerpt);
      if (joined !== expected && compact(joined) !== compact(expected)) {
        throw new Error(`mark text mismatch for comment ${c.id}: ${joined.slice(0, 120)}`);
      }
    }

    for (const c of comments) {
      const beforeScrolls = await page.evaluate(() => window.__kbScrolls.length);
      await page.evaluate(id => {
        document.querySelector(`#kb-cmt-${id}`).dispatchEvent(new MouseEvent("click", { bubbles: true }));
      }, c.id);
      const markId = excerptId(c.excerpt);
      await page.waitForFunction(id => {
        return Array.from(document.querySelectorAll(`mark.kb-comment-highlight[data-excerpt-id="${id}"]`))
          .some(mark => mark.classList.contains("kb-mark-pulse"));
      }, markId, { timeout: 3000 });
      const afterScrolls = await page.evaluate(() => window.__kbScrolls.length);
      if (afterScrolls <= beforeScrolls) throw new Error(`card click did not scroll for comment ${c.id}`);
    }

    console.log("PASS anchor overlap regression: all 4 real excerpts create marks and card clicks scroll");
  } finally {
    await context.close();
    server.close();
  }
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
