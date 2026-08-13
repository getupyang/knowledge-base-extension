# 2A.4 characterization：AI 回复前的记忆装载（场景清单 C 组）
# 边界纪律：只测确定性——挑选稳定(C1)/来源证据(C2)/禁跨用户(C3)/上限(C4)。
# "挑得准不准"是评测层（replay+benchmark）的事，不在此测。
import sqlite3
from datetime import datetime, timedelta


QUERY = "记忆评测的回放设计怎么做"

# 装载器的每类上限（对齐 agent_api._build_context_pack_for_query 的调用参数）
LIMIT_SKILLS = 4
LIMIT_EPISODIC = 12
LIMIT_EXPOSURE = 8


def _seed_corpus(agent_api, n=20):
    """合成语料（幂等，只种一次）：n 条相关批注 + 1 条无关批注 + 2 条工作方式 + 2 页缓存。
    Codex review #12：技能/页面暴露类别不种数据的话，上限断言在 0 上空转。"""
    conn = sqlite3.connect(agent_api.DB_PATH)
    already = conn.execute(
        "SELECT COUNT(*) FROM comments WHERE page_url LIKE 'https://corpus.test/%'"
    ).fetchone()[0]
    if already:
        conn.close()
        return
    now = datetime.now().isoformat()
    base = datetime.now() - timedelta(days=30)
    rows = []
    for i in range(n):
        ts = (base + timedelta(days=i)).isoformat()
        rows.append((
            f"https://corpus.test/p{i}", f"记忆评测第{i}篇",
            "回放系统的冻结边界", f"记忆评测的回放设计要点 {i}：策略回放与冻结边界", ts, ts,
        ))
    ts = (base + timedelta(days=n)).isoformat()
    rows.append(("https://corpus.test/unrelated", "菜谱", "番茄炒蛋", "晚饭做什么", ts, ts))
    conn.executemany(
        "INSERT INTO comments (page_url, page_title, selected_text, comment, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    # 工作方式（working_skills 需挂一个 generation）
    cur = conn.execute(
        "INSERT INTO skill_generations (kind, trigger_reason, trigger_payload, "
        "source_rules_count, llm_model, job_id, created_at) "
        "VALUES ('skills', 'test_seed', '{}', 0, 'fake', 0, ?)", (now,),
    )
    gen_id = cur.lastrowid
    for i, (name, desc) in enumerate([
        ("回放评测优先", "改记忆策略前先在冻结数据上回放评测"),
        ("设计先给冻结边界", "回放设计先明确冻结哪段流水线"),
    ]):
        conn.execute(
            "INSERT INTO working_skills (name, description, evidence_rule_ids, status, "
            "generation_id, created_at) VALUES (?, ?, '[]', 'active', ?, ?)",
            (name, desc, gen_id, now),
        )
    # 页面暴露（page_cache 全文含查询词）
    for i in range(2):
        conn.execute(
            "INSERT INTO page_cache (page_url, page_title, full_text, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"https://exposure.test/p{i}", f"回放评测资料{i}",
             f"关于记忆评测的回放设计与冻结边界的长文 {i}", now, now),
        )
    conn.commit()
    conn.close()


def test_c1_same_query_same_pack(hermetic):
    """C1：同样输入问两次 → 装载的记忆是同一批、同一顺序（不随机漂移）"""
    agent_api = hermetic.agent_api
    _seed_corpus(agent_api)
    p1 = agent_api._build_context_pack_for_query(QUERY)
    p2 = agent_api._build_context_pack_for_query(QUERY)
    assert p1["episodic_comment_ids"] == p2["episodic_comment_ids"], "历史批注挑选应稳定"
    assert p1["selected_skill_ids"] == p2["selected_skill_ids"], "工作方式挑选应稳定"
    assert p1["exposure_page_ids"] == p2["exposure_page_ids"], "页面暴露挑选应稳定"
    assert p1["context_md"] == p2["context_md"], "拼出的上下文文本应逐字一致"


def test_c2_selection_evidence_traceable(hermetic):
    """C2：每类装载都有 selection_reasons 来源证据；装载的批注 id 都真实存在"""
    agent_api = hermetic.agent_api
    _seed_corpus(agent_api)
    pack = agent_api._build_context_pack_for_query(QUERY)
    reasons = pack["selection_reasons"]
    for key in ("identity", "skills", "episodic", "exposure", "memory_map", "mode"):
        assert key in reasons, f"selection_reasons 缺 {key}——装载理由不可追溯"
    conn = sqlite3.connect(agent_api.DB_PATH)
    for cid in pack["episodic_comment_ids"]:
        row = conn.execute("SELECT 1 FROM comments WHERE id=?", (cid,)).fetchone()
        assert row, f"装载了不存在的批注 id={cid}（幻影记忆）"
    conn.close()


def test_c3_no_cross_user_private_context(hermetic):
    """C3：禁串户——数据目录之外的私有上下文文件一律不信任（agent_api 与 worker 口径一致）"""
    from pathlib import Path
    repo_file = str(Path(__file__).resolve().parents[1] / "project_context.md")  # 仓库内路径
    for mod in (hermetic.agent_api, hermetic.worker):
        prov = mod._private_context_provenance(repo_file)
        assert not prov.get("trusted"), f"{mod.__name__}: 仓库内文件不应被信任（串户/投毒入口）"
        prov2 = mod._private_context_provenance(str(mod.USER_PROFILE_PATH))
        assert prov2.get("trusted"), f"{mod.__name__}: 本用户数据目录内文件应被信任"


def test_c4_per_category_limits(hermetic):
    """C4：记忆多时按类别上限装载，不整库塞入"""
    agent_api = hermetic.agent_api
    _seed_corpus(agent_api)  # 20 条相关 > episodic 上限 12
    pack = agent_api._build_context_pack_for_query(QUERY)
    assert 0 < len(pack["episodic_comment_ids"]) <= LIMIT_EPISODIC, \
        f"历史批注装载 {len(pack['episodic_comment_ids'])} 条，应在 (0, {LIMIT_EPISODIC}]"
    # Codex review #12：技能/暴露类别必须真的有装载（0 条时上限断言是空转）
    assert 0 < len(pack["selected_skill_ids"]) <= LIMIT_SKILLS, \
        f"工作方式装载 {len(pack['selected_skill_ids'])} 条，应在 (0, {LIMIT_SKILLS}]"
    assert 0 < len(pack["exposure_page_ids"]) <= LIMIT_EXPOSURE, \
        f"页面暴露装载 {len(pack['exposure_page_ids'])} 条，应在 (0, {LIMIT_EXPOSURE}]"
    assert pack["token_budget_used"] >= 1
    assert pack["context_md"].startswith("## 记忆问答装载的上下文")
