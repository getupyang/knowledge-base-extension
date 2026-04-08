# 知识库助手 Chrome 插件 — 项目上下文

**每次开新 session 必读完这份文件，再开始任何工作。**

---

## 一、这个产品是什么（愿景，不能忘）

这不是一个"保存网页"工具。

这是**创业方向「意图-行动缺口」的 L1 采集层**——当用户在浏览器阅读时划线的那一刻，是意图最真实的信号。插件捕捉这个信号，触发 AI 深度处理，把"看了但没做"变成"看了就做"。

用户本人是第一个用户。产品的使用效果就是 pitch 故事。

**核心体验（北极星）：**
用户在任意网页划线 → 意图弹框出现 → 选择或输入意图类型 → AI 立即深度处理 → 结果沉淀到本地 + Notion

---

## 二、技术栈

- Chrome Extension Manifest V3，纯 JS（无构建工具）
- 后端：`agent_api.py`（FastAPI，端口 8766，在 `~/research/` 目录下）
- 本地存储：SQLite（source of truth，路径 `~/research/comments.db`）
- 展示层：Notion（human-readable view，不是主存储）
- AI：通过 `claude -p --dangerously-skip-permissions` 调用，路径从 `~/.kb_config` 读取

**密钥管理（2026-04-08 更新）：**
- 密钥统一存放在 `~/.kb_config`（chmod 600，不进 git）
- `background/index.js` 顶部有 `HARDCODED_CONFIG` 作为 fallback（此文件已加入 .gitignore）
- `agent_api.py` 启动时自动读取 `~/.kb_config` 注入环境变量

---

## 三、目录结构

```
knowledge-base-extension/
├── manifest.json
├── src/
│   ├── background/index.js   — API 调用、消息路由（顶部 HARDCODED_CONFIG 有密钥，.gitignore）
│   ├── content/index.js      — 页面注入：划线检测、评论区 UI、debug 面板
│   ├── collectors/claude.js  — Claude.ai 对话自动采集（有 bug，低优先级）
│   ├── sidepanel/            — 侧边栏 AI 对话界面（仍走 OpenRouter，独立于评论区）
│   └── popup/                — 配置页（chrome.storage bug，已绕过）
~/research/
├── agent_api.py              — 评论区 agent 后端（端口 8766）
├── company_culture.md        — L1 文化层：AI 输出行为规范，所有 agent 调用时注入
├── project_context.md        — L2 项目上下文：创业背景、竞品结论、当前进展
├── project_snapshot.md       — L2 动态层（待创建）：当前阶段、本周焦点、阻塞项
├── start.sh                  — 一键启动（读 ~/.kb_config）
├── setup.sh                  — 首次安装脚本（检查依赖、写 ~/.kb_config）
├── comments.db               — SQLite source of truth
└── .kb_config（~/.kb_config）— 密钥+路径配置，chmod 600，不进 git
```

---

## 四、当前功能状态（2026-04-08 更新）

| 功能 | 状态 | 备注 |
|------|------|------|
| 右键保存到 Notion | ✅ 可用 | background.js HARDCODED_CONFIG 已修复 token 为空 bug |
| AI 对话侧边栏 | ✅ 可用 | 走 agent_api（已从 OpenRouter 迁移） |
| 评论区发送→agent_api | ✅ 已修复 | submitComment 同时写 localStorage + POST 8766 |
| 召唤 AI 按钮 | ✅ 已修复 | 走 agent_api 轮询，不再走 OpenRouter |
| selected_text 捕获 | ✅ 已修复 | mouseup 提前保存，submitComment fallback |
| Debug 面板 | ✅ 已实现 | 评论卡片底部折叠，含 elapsed/tokens/context layers |
| L1 文化层注入 | ✅ 已实现 | company_culture.md，所有 agent 调用时加载 |
| claude -p 稳定调用 | ✅ 已修复 | 用绝对路径+~/.kb_config，重启后不再失败 |
| Claude.ai 自动采集 | ⚠️ 有 bug | 流式输出提前触发，历史对话重复保存，低优先级 |
| popup 配置页 | ❌ chrome.storage bug | 已绕过，HARDCODED_CONFIG 够用 |
| 划线高亮持久化 | ❌ 未做 | 刷新后消失，P1 |
| 任意网页可用 | ❌ 未做 | 目前主要在 localhost:8765，P1 |
| L3 文章上下文 | ❌ 未做 | Readability.js 抓全文，**下一个最高优先级** |
| L4 agent 角色分化 | ⚠️ 部分 | 四个 prompt 存在，但人格区分度低，需要重写 |
| 导师 Agent | ❌ 未做 | @导师 触发，读近期批注后提灵魂问题 |

---

## 五、四层 Context 架构（核心设计，2026-04-08 确定）

这是本产品的策略核心，每次 agent 调用的信息质量由这四层决定：

| 层 | 含义 | 实现 | 状态 |
|---|---|---|---|
| L1 文化层 | AI 输出行为规范（禁止废话、信息密度、呈现方式） | `company_culture.md` | ✅ 已上线 |
| L2 项目层 | 创业背景 + 当前阶段快照 | `project_context.md` + `project_snapshot.md`（待建） | ⚠️ 静态 |
| L3 文章层 | 当前页面全文/摘要 + 用户历史划线 | Readability.js（待实现） | ❌ 缺失 |
| L4 技能层 | 每个 agent 的独立人格定义 | AGENT_PROMPTS（需重写） | ⚠️ 薄弱 |

**L3 是当前最高优先级**：agent 回答质量差的根本原因是不知道文章说了什么。

---

## 六、意图路由系统

用户评论用 @xxx 触发不同 agent（在 agent_api.py 的 AGENT_PROMPTS 里）：

| 意图 | 触发词 | 当前状态 | 期望人格 |
|------|--------|---------|---------|
| 竞品分析 | @竞品 | ⚠️ prompt 较通用 | 情报官：找差异化机会，不做功能列表 |
| 思辨 | @思辨 | ⚠️ prompt 较通用 | 苏格拉底：找假设、提反例、结尾必须是问号 |
| 调研 | @调研 | ⚠️ prompt 较通用 | 侦察员：强制 WebSearch，事实/判断分层 |
| 解释 | @解释 | ⚠️ prompt 较通用 | 翻译官：类比+类比的局限，给可操作示例 |
| 导师 | @导师 | ❌ 未实现 | 发现盲区，一次只问一个灵魂问题 |

---

## 七、数据流（完整）

```
用户划线（content.js mouseup 捕获 selection）
    → 右键"评论" → 评论面板打开
    → 用户写评论（支持 @xxx 路由）
    → submitComment()
        → 写 localStorage（本地持久化）
        → POST http://localhost:8766/comments（agent_api.py）
            → 注入：L1文化层 + L2项目层 + Notion记忆（15条）
            → 后台线程：claude -p（绝对路径，~/.kb_config）
            → 结果写 SQLite replies 表（含 debug_meta JSON）
    → 前端轮询（3s×30次=90s）拿结果
    → render() 展示 AI 回复 + debug 折叠面板
    → 异步写 Notion（展示层）
```

---

## 八、启动方式

```bash
# 首次安装（任何机器）
bash ~/research/setup.sh

# 每次开机（必须在使用插件前运行）
~/research/start.sh

# 验证服务状态
curl http://localhost:8765      # 知识库界面
curl http://localhost:8766/health  # agent_api
```

---

## 九、开发规范

### 9.1 不让用户当测试员
改了代码，Claude 自己验证，不说"你试试"。
验证方式：curl agent_api + 轮询结果。

### 9.2 标准验收命令

```bash
# agent 管道验证
COMMENT_ID=$(curl -s -X POST http://localhost:8766/comments \
  -H "Content-Type: application/json" \
  -d '{"page_url":"http://test.com","page_title":"测试","selected_text":"测试文本","comment":"@解释 这是验证"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
# 轮询
for i in $(seq 1 20); do
  R=$(curl -s http://localhost:8766/comments/$COMMENT_ID | python3 -c "
import json,sys; d=json.load(sys.stdin)
a=[r for r in d['replies'] if r['author']=='agent']
if a: print(a[-1]['content'][:100])
" 2>/dev/null); [ -n "$R" ] && echo "✓ $R" && break; echo "等待...$i"; sleep 3; done
```

### 9.3 修复 bug 的规则
1. 先复现（能稳定复现才能修）
2. 修改范围最小化，不顺手重构
3. 修完跑验收命令
4. 3 次未解决 → 停下来分析根因

### 9.4 阶段纪律
- 实现阶段不扩展需求 → 记录到优先级列表，本次不加
- 遇到阻断（API 报错、CORS、权限）立即播报，不默默换方案

---

## 十、优先级（下一个 session 从这里开始）

**P0（最高优先）：**
1. **L3 文章上下文**：集成 Readability.js，content.js 页面加载时提取全文，agent 调用时注入
2. **L4 agent 人格重写**：按五个人格模板重写 AGENT_PROMPTS（模板在 company_culture.md 旁边的设计文档里）

**P1（接下来）：**
3. 划线高亮持久化（刷新后消失）
4. project_snapshot.md 建立（L2 动态层）
5. 导师 Agent 实现（@导师 触发）

**P2（以后）：**
6. 任意网页可用（不只是 localhost:8765）
7. Claude.ai 采集器 bug 修复
8. Notion 重复写入/截断问题修复

**暂时搁置：**
- popup 配置页（HARDCODED_CONFIG 绕过方案够用）

---

## 十一、session 结束时（Claude 主动执行）

1. 更新"当前功能状态"表
2. 更新优先级列表
3. 如有新发现的 bug，写入此文件
4. 更新 memory/project_knowledge_base_extension.md
