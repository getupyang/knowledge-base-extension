# 决策：Repo 重构为可分发结构

**日期：** 2026-04-09
**状态：** 已执行

## 决策内容

将原本分散在两个目录的代码合并为一个可完整分发的 repo：

- `~/research/`（后端服务）+ `~/Documents/ai/coding/knowledge-base-extension/`（插件）→ 统一到 `knowledge-base-extension/`
- 后端文件迁入 `backend/` 子目录
- 密钥管理改为：后端走 `~/.kb_config`，插件走 `chrome.storage.local`，两者都不进 git

## 背景

- 朋友也是 Mac + Claude Pro 用户，每人一套本地部署
- 原来 `background/index.js` 有 HARDCODED_CONFIG（含 Notion token），导致代码一直无法推 GitHub
- `~/research/` 没有 git 管理，无法版本控制和分发

## 关键约束

- 只剩 Notion 密钥需要配置（已移除 OpenRouter）
- `company_culture.md`（AI 行为规范）进 repo，通用
- `project_context.md` 不进 repo，每人自己填，template 进 repo
- `~/research/topics/`（私人 MD 文件）永远不进 repo，共享的是工作模式不是知识

## 影响

- 朋友安装：clone repo → `bash setup.sh`（引导填 Notion 配置）→ `bash start.sh`
- 本地工作台启动命令从 `~/research/start.sh` 改为 `~/Documents/ai/coding/knowledge-base-extension/start.sh`
- `~/research/` 目录保留，作为私人数据和文档的存储，不再是服务启动位置
