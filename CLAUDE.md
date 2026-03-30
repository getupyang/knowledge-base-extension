# 知识库助手 Chrome插件 - 开发规范

## 项目概述
Chrome插件，自动采集用户在各平台的阅读和AI对话行为，存入Notion知识库。

## 技术栈
- Chrome Extension Manifest V3，纯JS（无构建工具）
- Notion API（直接调用，token在background.js里）
- OpenRouter API（claude-3.5-sonnet）
- Playwright（自动化测试）

## 目录结构
- src/background/index.js — 所有API调用（Notion/OpenRouter），消息路由
- src/content/index.js — 页面注入：右键菜单、侧边栏UI
- src/collectors/claude.js — Claude.ai对话自动采集
- scripts/test-claude-collector.js — Playwright自动化测试

## 开发规范

### 修改代码后必须
1. 用Playwright跑对应的测试脚本验证
2. 确认Notion数据库有新记录才算通过
3. 不能说"应该好了"，必须有测试证据

### 验收标准（每个功能）
- Claude.ai采集：发一条新消息 → 4秒后 → Notion出现新记录，内容完整（非截断）
- 快速保存：选中文字右键 → 弹框输入想法 → Notion出现新记录
- AI对话侧边栏：右键AI对话后保存 → 多轮对话 → 点保存 → Notion出现完整Q&A

### Bug调试规范
1. 先用Playwright复现bug
2. 读控制台报错
3. 修复后用同一个测试脚本验证
4. 如果3次修改后仍未解决，停下来分析根本原因，不要继续猜

### Token效率规范
- 同一个Notion页面一个session只查询一次
- 如果同一个工具调用3次以上结果相似，停下来换思路
- 每个任务结束输出：完成了什么、测试结果是pass还是fail

## 当前已知问题
- Claude.ai采集器：流式输出期间会提前触发保存（内容不完整）
- Claude.ai采集器：历史对话有时会被重复保存

## 接下来的版本规划
- v0.3（当前）：Claude.ai自动采集
- v0.4：ChatGPT自动采集
- v0.5：Gemini自动采集
