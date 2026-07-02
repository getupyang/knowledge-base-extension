#!/usr/bin/env python3
"""
评测回放模块（eval_replay）— 新策略分支的可选标配。

开发协议（2026-07-01 定）：
  - 「要参与 AB 测试」的策略分支，开发时按约定 include 本模块。
  - 纯前端 / 不需 AB 测的改动，不加本模块。
  - 本模块是评测脚手架，随策略分支走；它 import 的 llm_client 就是【本分支的】，
    因此不同分支起独立进程时，回放的就是各自分支的真实策略代码（进程隔离=版本隔离）。

它做什么：
  暴露 POST /eval/replay —— 收一个【冻结的 final_prompt】+ 策略参数（search_mode），
  直接调本分支的 client.generate_text，返回真实回复 + 完整 trace。
  绕过 router（freeze_memory 模式：喂冻结 prompt，只变被测参数）。

它【不】做什么（红线）：
  - 不写线上日志（不碰 llm_call_ledger / llm_request_snapshots）。结果只返回给 runner，
    runner 落进独立 eval.db。→ 回放绝不污染线上数据。
  - 不改产品任何既有函数。纯新增文件。

挂载方式（在 agent_api.py 或独立启动脚本里加一行）：
    from eval_replay import eval_router
    app.include_router(eval_router)
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

# import 本分支的 llm_client（进程隔离保证是本分支的代码）
from llm_client import get_llm_client, get_llm_status

eval_router = APIRouter(prefix="/eval", tags=["eval"])


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _git_sha() -> str:
    """本进程加载的代码版本，用于 trace 可溯源（第 0 原则）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


class ReplayRequest(BaseModel):
    final_prompt: str                       # 冻结的完整 prompt（B段记忆+C段策略成品）
    system_prompt: str = ""                 # 冻结的 system prompt
    search_mode: str = "provider_auto"      # ★被测参数：always-search 传 "required"
    timeout: int = 1800
    strategy_id: str = ""                    # 可读策略名，仅回显便于溯源
    # 期望的 prompt sha，用于 runner 校验「喂进去的==source」。留空则不校验。
    expect_prompt_sha256: Optional[str] = None


class ReplayResponse(BaseModel):
    ok: bool
    strategy_id: str
    reply_text: str
    reply_sha256: str
    sent_prompt_sha256: str                 # 实际发给 LLM 的 prompt 哈希
    prompt_integrity: str                   # 'match' / 'mismatch' / 'unchecked'
    runner_git_sha: str
    provider: str = ""
    model: str = ""
    actual_search_called: Optional[bool] = None
    search_mode: str = ""
    elapsed_s: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    status: str = ""
    error: str = ""
    raw_meta: dict = {}


@eval_router.get("/replay/ping")
def replay_ping():
    """健康探针：runner 用它确认这个策略进程起来了、provider 是谁、代码版本。"""
    status = get_llm_status()
    return {
        "ok": True,
        "git_sha": _git_sha(),
        "provider": status.get("selected_provider"),
        "provider_config": status.get("provider_config"),
    }


@eval_router.post("/replay", response_model=ReplayResponse)
def replay(req: ReplayRequest) -> ReplayResponse:
    """freeze_memory 回放：喂冻结 prompt，按 search_mode 跑本分支真实策略，返回回复+trace。"""
    git_sha = _git_sha()
    sent_sha = _sha256(req.final_prompt)

    # 完整性校验：runner 传了期望 sha 就核对，证明「喂的就是 source frozen prompt」
    if req.expect_prompt_sha256:
        integrity = "match" if sent_sha == req.expect_prompt_sha256 else "mismatch"
    else:
        integrity = "unchecked"

    client = get_llm_client()
    started = time.time()
    try:
        # 直接调 generate_text，绕过产品 _call_llm_with_meta 里硬编的 provider_auto，
        # 让被测策略的 search_mode 真正生效。不写任何线上日志。
        content = client.generate_text(
            req.final_prompt,
            system_prompt=req.system_prompt,
            timeout=req.timeout,
            search_mode=req.search_mode,
        )
        meta = dict(client.last_call_meta or {})
        return ReplayResponse(
            ok=True,
            strategy_id=req.strategy_id,
            reply_text=content,
            reply_sha256=_sha256(content),
            sent_prompt_sha256=sent_sha,
            prompt_integrity=integrity,
            runner_git_sha=git_sha,
            provider=meta.get("provider", ""),
            model=meta.get("model", ""),
            actual_search_called=meta.get("actual_search_called"),
            search_mode=meta.get("search_mode", req.search_mode),
            elapsed_s=round(time.time() - started, 2),
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            cost_usd=meta.get("cost_usd"),
            status=meta.get("status", "success"),
            raw_meta=meta,
        )
    except Exception as e:
        meta = dict(getattr(client, "last_call_meta", {}) or {})
        return ReplayResponse(
            ok=False,
            strategy_id=req.strategy_id,
            reply_text="",
            reply_sha256="",
            sent_prompt_sha256=sent_sha,
            prompt_integrity=integrity,
            runner_git_sha=git_sha,
            provider=meta.get("provider", ""),
            search_mode=req.search_mode,
            elapsed_s=round(time.time() - started, 2),
            status="error",
            error=f"{type(e).__name__}: {str(e)[:400]}",
            raw_meta=meta,
        )
