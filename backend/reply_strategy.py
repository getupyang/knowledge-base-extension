#!/usr/bin/env python3
"""
策略：search-directive-v1 —— 指令层强制搜索。

设计原则（对照历史教训）：
  - 零判断：不判断「这个 case 要不要搜」（那正是 planner 过拟合的根源）。
    对所有召唤一视同仁地注入强制搜索指令。
  - 参数层不够就上指令层：实测 search_mode=required 在 claude_code / codex_cli
    上都不会真的触发搜索（工具挂上了但模型自己决定不用）。本策略改在
    system prompt 里显式下达检索要求——模型对指令的服从远强于对工具的主动使用。
  - 例外由模型自己判断：指令允许「页面已给全部所需信息时可不搜」，
    把边界判断交给大模型，而不是我们的正则。

接口（参评分支协议）：eval_replay 自动发现并调用 transform。
"""
from __future__ import annotations

# 策略版本标识：线上 debug_meta / 回放台账都用它归因
STRATEGY_ID = "search-directive-v1"

SEARCH_DIRECTIVE = """
## 检索要求（本策略强制）
回答前必须先用 web 搜索工具检索：
- 用户要求「展开/详细说说/调研」的对象（原帖、产品、论文等），必须找到一手来源，
  逐条给出实际内容，并附可打开的来源链接。
- 涉及时效性信息（近期动态、热度、版本）必须以检索结果为准，不得用训练数据冒充。
- 唯一例外：用户只是表达观点/质疑、且页面上下文已包含回答所需全部信息时，
  可不检索，但需在回复开头一句话说明「本次未检索，基于页面内容回应」。
搜不到就明说搜不到，禁止编造来源。
""".strip()


def transform(system_prompt: str, final_prompt: str, search_mode: str = "") -> tuple:
    """在 system prompt 尾部注入强制搜索指令。冻结的 final_prompt 不动。"""
    merged = (system_prompt or "").rstrip() + "\n\n" + SEARCH_DIRECTIVE
    return merged, final_prompt
