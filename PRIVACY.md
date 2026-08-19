# Margin 隐私政策 / Privacy Policy

最后更新 / Last updated: 2026-08-19

## 一句话

你的划线、批注、网页内容**只存在你自己的电脑上**。Margin 没有自己的服务器保存你的内容。

## 数据地图：每类数据从哪来、存在哪、给谁、怎么删

| 数据 | 怎么产生 | 存在哪 | 会发给谁 | 怎么删除 |
|---|---|---|---|---|
| 划线、批注、与 AI 的对话 | 你在网页上主动划线/写批注 | 你电脑上的浏览器存储 + 你本机的 Margin 引擎（`~/.knowledge-base-extension`） | **不发给任何人**。你的 AI 调用由你本机引擎直连你自己的 AI 服务（Claude Code / Codex / 你自己的 API Key） | 删除本机数据目录；卸载插件清除浏览器存储 |
| Notion 备份（可选） | 你在插件里主动开启并提供自己的 Notion 授权 | 你自己的 Notion 数据库 | 只发给**你自己的** Notion 账户 | 在插件里关闭开关；在你的 Notion 里删除 |
| 配对令牌 | 插件与你本机引擎首次连接时自动生成 | 你电脑本地（浏览器存储 + `~/.kb_config`，权限 600） | 只在你电脑内部使用（浏览器 ↔ 本机引擎） | 删除 `~/.kb_config` 中对应行 |
| 匿名使用统计 | **你首次打开插件明确同意后**才开始产生 | 先存本机，随后发送 | Margin 的统计服务。**字段白名单机制**：只有约 30 个经审查的脱敏字段（次数、耗时档位、开关状态）能被发送，任何正文、URL、划线内容在技术上无法进入统计 | 统计不含可关联到你个人身份的信息；如需清除可通过下方联系方式申请 |
| 问题报告（可选） | 你主动点击"报告问题"并确认 | 先存本机 | 默认只含技术诊断信息（版本、耗时、错误码）。**任何正文内容都需要你逐项勾选并再次确认**才会附带 | 不提交即不产生 |

## 我们不做的事

- 不采集你的浏览历史（插件只在你主动划线时工作）
- 不把你的任何正文内容发送到 Margin 的服务器
- 不出售、共享任何数据给第三方
- 不使用任何广告或跟踪 SDK

## 开源

Margin 的全部代码开源可查：https://github.com/getupyang/knowledge-base-extension
上述承诺可以在代码中逐条验证（埋点白名单：`backend/agent_api.py` 的 `_TELEMETRY_ALLOWED_KEYS`）。

## 联系

问题或数据请求：https://github.com/getupyang/knowledge-base-extension/issues

---

## English Summary

Margin stores your highlights, annotations, and AI conversations **only on your own computer**. AI calls go directly from your local engine to your own AI provider. Optional Notion backup goes only to your own Notion account. Anonymous usage statistics (opt-in on first use) are restricted by an allowlist of ~30 reviewed, content-free fields — page content, URLs, and annotation text technically cannot be transmitted. No ads, no tracking SDKs, no selling of data. Full source code: https://github.com/getupyang/knowledge-base-extension
