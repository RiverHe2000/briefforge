import asyncio
import copy
import json

import pytest

from briefforge.demo import COMPANIES, seed_demo
from briefforge.engine import (
    DATE_GUARD_REASON,
    PRICE_UNIT_GUARD_REASON,
    EditorOutput,
    PlanOutput,
    ResearchEngine,
    ResearchOutput,
    ReviewOutput,
    _compile_price_claim,
    _ground_review_issues,
    _relevant_sources,
    _source_conflict_issues,
    _temporal_context,
    run_research,
    validate_claims,
)
from briefforge.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{tmp_path / 'db.sqlite'}", tmp_path)


async def execute(store, project, architecture="multi"):
    run = store.create_run(project["id"], {"mode": "replay", "architecture": architecture})
    report = await run_research(store, run["id"])
    store.update_run(run["id"], {"status": "completed"})
    return run, report


async def test_dynamic_research_finds_old_sso_and_corrects_it(store):
    project = seed_demo(store)
    run, report = await execute(store, project)
    sso = next(c for c in report["claims"] if c["subject"] == COMPANIES[0] and c["dimension"] == "SSO")
    assert sso["status"] == "supported"
    assert "企业版支持" in sso["statement"] and "团队版不支持" in sso["statement"]
    events = store.list_events(run["id"])
    correction = next(e for e in events if e["type"] == "claim_corrected")
    assert "所有套餐均不支持" in correction["payload"]["before"]
    assert "企业版支持" in correction["payload"]["after"]
    assert {t["role"] for t in store.list_tasks(run["id"])} == {
        "coordinator",
        "industry",
        "competitor",
        "commercial",
        "verifier",
        "editor",
    }
    assert report["mode"] == "replay" and report["synthetic"]
    assert store.get_run(run["id"])["request_count"] == 0
    assert (store.data_dir / "graph-checkpoints.sqlite").exists()
    for claim in report["claims"]:
        for evidence in claim["evidence"]:
            assert evidence["quote"] in store.get_source(evidence["source_id"])["text"]


async def test_unresolved_same_date_conflict_is_retained_and_loops_bounded(store):
    project = seed_demo(store, "test-contradiction")
    run, report = await execute(store, project)
    sso = next(c for c in report["claims"] if c["subject"] == COMPANIES[0] and c["dimension"] == "SSO")
    assert sso["status"] == "uncertain"
    assert any("同日期" in issue for issue in report["unresolved"])
    tasks = store.list_tasks(run["id"])
    assert max(t["round"] for t in tasks) == 2
    assert len(tasks) <= 24


async def test_baselines_have_distinct_behavior_and_no_hidden_dynamic_tasks(store):
    project = seed_demo(store)
    single_run, single = await execute(store, project, "single")
    pipeline_run, pipeline = await execute(store, project, "pipeline")
    assert {t["role"] for t in store.list_tasks(single_run["id"])} == {"single", "editor"}
    assert not any(t["round"] > 0 for t in store.list_tasks(pipeline_run["id"]))
    sso = lambda report: next(
        c for c in report["claims"] if c["subject"] == COMPANIES[0] and c["dimension"] == "SSO"
    )
    assert "所有套餐均不支持" in sso(single)["statement"]
    assert sso(pipeline)["status"] == "uncertain"


async def test_partial_price_update_keeps_unaffected_claims_and_immutable_old_report(store):
    project = seed_demo(store)
    _, old_report = await execute(store, project)
    old_snapshot = copy.deepcopy(old_report)
    source = next(
        s
        for s in store.list_sources(project["id"])
        if s["competitor"] == COMPANIES[0] and s["kind"] == "pricing"
    )
    store.add_source(project["id"], {**source, "text": source["text"].replace("USD 12/", "USD 14/")})
    new_run, new_report = await execute(store, store.get_project(project["id"]))
    assert "USD 14/" in new_report["comparison"][0]["price"]
    assert new_report["comparison"][1] == old_report["comparison"][1]
    assert store.get_report(old_report["id"])["content_hash"] == old_snapshot["content_hash"]
    assert (
        store.get_report(old_report["id"])["comparison"][0]["price"] == old_snapshot["comparison"][0]["price"]
    )
    assert any("局部更新" in change for change in new_report["changes"])
    assert not any(t["target"] == COMPANIES[1] for t in store.list_tasks(new_run["id"]))
    assert not any(t["role"] == "competitor" for t in store.list_tasks(new_run["id"]))
    reuse = [e for e in store.list_events(new_run["id"]) if e["type"] == "reuse_planned"]
    assert len(reuse) == 1
    assert reuse[0]["payload"]["affected_claim_keys"] == [f"{COMPANIES[0]}::价格"]
    assert f"{COMPANIES[1]}::SSO" in reuse[0]["payload"]["preserved_claim_keys"]


async def test_completed_tasks_reused_after_failed_parallel_node(store, monkeypatch):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "replay", "architecture": "multi"})
    original = ResearchEngine.research_one
    failed = False

    async def fail_once(self, spec, state, round_=0):
        nonlocal failed
        if spec["target"] == COMPANIES[1] and not failed:
            failed = True
            raise RuntimeError("injected process interruption")
        return await original(self, spec, state, round_)

    monkeypatch.setattr(ResearchEngine, "research_one", fail_once)
    with pytest.raises(RuntimeError, match="injected"):
        await run_research(store, run["id"])
    completed = {t["id"] for t in store.list_tasks(run["id"]) if t["status"] == "completed"}
    assert len(completed) >= 3
    report = await run_research(store, run["id"])
    assert report["id"]
    starts = [e["payload"]["task_id"] for e in store.list_events(run["id"]) if e["type"] == "task_started"]
    assert all(starts.count(task_id) == 1 for task_id in completed)
    assert (await run_research(store, run["id"]))["id"] == report["id"]
    assert len(store.list_reports(project["id"])) == 1
    reused = [e["payload"]["task_id"] for e in store.list_events(run["id"]) if e["type"] == "task_reused"]
    assert reused and set(reused).issubset(completed) and len(reused) == len(set(reused))


async def test_replay_ignores_instructions_embedded_in_source(store):
    project = seed_demo(store, "test-injection")
    _, report = await execute(store, project)
    rendered = report["executive_summary"] + " ".join(section["body"] for section in report["sections"])
    assert "BRIEFFORGE_INJECTION_ACCEPTED" not in rendered
    assert "100%" not in rendered


def test_quote_validation_rejects_fabricated_and_cross_competitor_citations():
    source = {"id": "s1", "competitor": "A", "text": "Price: USD 12 per month."}
    claim = {
        "subject": "A",
        "dimension": "价格",
        "statement": "USD 1 per month",
        "status": "supported",
        "evidence": [{"source_id": "s1", "quote": "USD 1 per month", "locator": ""}],
        "value": "1",
        "conditions": [],
    }
    checked = validate_claims([claim], [source], {"A"})[0]
    assert checked["status"] == "uncertain" and checked["evidence"] == []
    claim.update(subject="B", evidence=[{"source_id": "s1", "quote": source["text"], "locator": ""}])
    assert validate_claims([claim], [source], {"B"})[0]["status"] == "uncertain"


async def test_agent_concurrency_never_exceeds_three(store):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "replay"})
    engine = ResearchEngine(store, run["id"])
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {}

    await asyncio.gather(*(engine.task("competitor", str(i), str(i), 0, work) for i in range(8)))
    assert peak == 3


async def test_live_agent_reads_frozen_source_then_submits_valid_claim(store):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "live"})
    source = next(s for s in store.list_sources(project["id"]) if s["kind"] == "pricing")
    quote = next(line for line in source["text"].splitlines() if line.startswith("价格："))

    class ToolProvider:
        steps = 0

        async def tool_turn(self, messages, definitions, purpose):
            self.steps += 1
            if self.steps == 1:
                assert "text" not in messages[-1]["content"]
                return {
                    "name": "read_sources",
                    "arguments": {"source_ids": [source["id"]]},
                    "call_id": "r",
                    "message": {"role": "assistant", "content": "read"},
                }
            assert quote in messages[-1]["content"]
            return {
                "name": "submit_claims",
                "arguments": {
                    "claims": [
                        {
                            "subject": source["competitor"],
                            "dimension": "价格",
                            "statement": quote,
                            "status": "supported",
                            "evidence": [{"source_id": source["id"], "quote": quote, "locator": "价格段落"}],
                            "conditions": [],
                            "value": None,
                        }
                    ]
                },
            }

    provider = ToolProvider()
    engine = ResearchEngine(store, run["id"], provider=provider)
    claims = await engine.research_tools("commercial", {"question": "价格"}, [source], "test")
    assert provider.steps == 2
    assert validate_claims(claims, [source], {source["competitor"]})[0]["status"] == "supported"


async def test_third_tool_step_must_submit_and_unread_citation_is_removed(store):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "live"})
    source = store.list_sources(project["id"])[0]

    class ToolProvider:
        steps = 0

        async def tool_turn(self, messages, definitions, purpose):
            self.steps += 1
            if self.steps < 3:
                return {
                    "name": "search_sources",
                    "arguments": {"query": "nonexistent-secret"},
                    "call_id": str(self.steps),
                    "message": {"role": "assistant", "content": "search"},
                }
            assert set(definitions) == {"submit_claims"}
            return {
                "name": "submit_claims",
                "arguments": {
                    "claims": [
                        {
                            "subject": source["competitor"],
                            "dimension": "定位",
                            "statement": "invented",
                            "status": "supported",
                            "evidence": [
                                {"source_id": source["id"], "quote": source["text"][:30], "locator": ""}
                            ],
                            "conditions": [],
                            "value": None,
                        }
                    ]
                },
            }

    provider = ToolProvider()
    claims = await ResearchEngine(store, run["id"], provider=provider).research_tools(
        "competitor", {}, [source], "bounded"
    )
    assert provider.steps == 3
    assert claims[0]["evidence"] == []


async def test_public_discovery_fetches_industry_bodies_and_never_uses_snippets(store, monkeypatch):
    project = store.create_project(
        {
            "title": "公开研究",
            "question": "比较团队知识工具",
            "mode": "public",
            "competitors": ["A", "B", "C"],
            "dimensions": ["定位", "价格"],
        }
    )
    run = store.create_run(project["id"], {"mode": "live"})

    class SearchProvider:
        def __init__(self):
            self.queries = []

        async def search(self, query, purpose):
            self.queries.append(query)
            return [
                {
                    "url": f"https://example.com/{len(self.queries)}",
                    "title": "Discovered",
                    "snippet": "DO_NOT_USE_SEARCH_SNIPPET_AS_EVIDENCE",
                }
            ]

    async def fetch(url):
        return {
            "url": url,
            "title": "Fetched body",
            "text": "This is the actual downloaded page body.",
            "published_at": None,
        }

    monkeypatch.setattr("briefforge.ingest.fetch_public_url", fetch)
    provider = SearchProvider()
    engine = ResearchEngine(store, run["id"], provider=provider)
    state = engine.initial_state()
    result = await engine.discover(state)
    sources = [store.get_source(sid) for sid in result["source_ids"]]
    assert len(provider.queries) == 4 and len(sources) == 4
    assert sum(not source["competitor"] for source in sources) == 1
    assert all(source["text"] == "This is the actual downloaded page body." for source in sources)


def test_run_uses_frozen_brief_and_empty_source_snapshot(store):
    project = store.create_project(
        {
            "title": "Original",
            "question": "Original",
            "mode": "public",
            "competitors": ["A"],
            "dimensions": ["价格"],
        }
    )
    run = store.create_run(project["id"], {"mode": "live"})
    store.update_project(project["id"], {"title": "Changed", "competitors": ["B"]})
    store.add_source(project["id"], {"title": "Late source", "text": "Late evidence", "competitor": "A"})
    engine = ResearchEngine(store, run["id"])
    assert engine.project["title"] == "Original"
    assert engine.initial_state()["source_ids"] == []


def temporal_case():
    old = {
        "id": "old",
        "competitor": "A",
        "title": "Historical security",
        "kind": "archive",
        "published_at": "2026-03-01T00:00:00+00:00",
        "text": "存档提示：本页记录当时的产品说明；当前条件请查看后续资料。\nSSO：所有套餐不支持 SAML SSO。",
    }
    new = {
        "id": "new",
        "competitor": "A",
        "title": "Current security",
        "kind": "security",
        "published_at": "2026-09-15T00:00:00+00:00",
        "text": "SSO：企业版支持 SAML SSO，团队版不支持；自 2026-09-15 生效。",
    }
    claim = {
        "subject": "A",
        "dimension": "SSO",
        "statement": new["text"],
        "status": "supported",
        "evidence": [{"source_id": "new", "quote": new["text"], "locator": "SSO"}],
        "conditions": [],
        "value": None,
    }
    return [old, new], claim


@pytest.mark.parametrize("field", ["statement", "value", "conditions"])
def test_unsupported_date_cannot_leak_through_any_claim_field(field):
    sources, claim = temporal_case()
    claim[field] = ["截至 2026 年 3 月 15 日"] if field == "conditions" else "截至 2026 年 3 月 15 日"
    checked = validate_claims([claim], sources, {"A"})[0]
    assert checked["status"] == "uncertain"
    assert checked["value"] is None and checked["conditions"] == [DATE_GUARD_REASON]
    assert "3 月 15" not in str(checked)
    issues, _ = _ground_review_issues([checked], sources, [])
    assert issues[0]["resolution"] == "followup" and issues[0]["category"] == "date"


def test_date_guard_accepts_equivalent_date_spelling_and_publication_metadata():
    sources, claim = temporal_case()
    claim["statement"] = "2026 年 9 月 15 日发布的资料显示企业版支持 SSO。"
    assert validate_claims([claim], sources, {"A"})[0]["status"] == "supported"
    claim["evidence"] = [{"source_id": "old", "quote": "SSO：所有套餐不支持 SAML SSO。", "locator": ""}]
    claim["statement"] = "2026 年 3 月 1 日的归档记载当时所有套餐不支持 SSO。"
    assert validate_claims([claim], sources, {"A"})[0]["status"] == "supported"


def test_explicit_historical_update_does_not_create_a_false_current_conflict():
    sources, claim = temporal_case()
    issue = {
        "subject": "A",
        "dimension": "SSO",
        "source_ids": ["old", "new"],
        "category": "current_conflict",
        "resolution": "followup",
        "reason": "2026年3月15日旧资料不支持，最新资料支持，存在冲突。",
    }
    issues, notices = _ground_review_issues([claim], sources, [issue])
    assert issues == [] and notices
    # Do not silently prefer newer evidence without explicit supersession.
    sources[0]["text"] = "SSO：所有套餐不支持 SAML SSO。"
    issues, notices = _ground_review_issues([claim], sources, [issue])
    assert len(issues) == 1 and "3月15日" not in issues[0]["reason"]
    assert "日期未获所引来源支持" in issues[0]["reason"]


def test_same_period_and_undated_conflicts_are_never_auto_resolved():
    sources, claim = temporal_case()
    conflict = {**sources[1], "id": "conflicting", "text": "SSO：所有套餐不支持 SAML SSO。"}
    issue = {
        "subject": "A",
        "dimension": "SSO",
        "source_ids": ["old", "new", "conflicting"],
        "category": "current_conflict",
        "resolution": "followup",
        "reason": "当前资料存在矛盾。",
    }
    assert _temporal_context([claim], [*sources, conflict]) == []
    issues, _ = _ground_review_issues([claim], [*sources, conflict], [issue])
    assert len(issues) == 1 and issues[0]["category"] == "current_conflict"
    assert set(issues[0]["source_ids"]) == set(issue["source_ids"])
    sources[1]["published_at"] = None
    assert _temporal_context([claim], sources) == []


def test_old_only_claim_requires_bounded_followup_despite_critic_silence():
    sources, claim = temporal_case()
    claim["evidence"] = [{"source_id": "old", "quote": "SSO：所有套餐不支持 SAML SSO。", "locator": ""}]
    claim["statement"] = claim["evidence"][0]["quote"]
    checked = validate_claims([claim], sources, {"A"})
    issues, _ = _ground_review_issues(checked, sources, [])
    assert issues[0]["category"] == "historical_update" and issues[0]["source_ids"] == ["new"]


def test_long_page_context_includes_relevant_tail_and_exact_citation_window():
    prefix = "Navigation and general marketing. " * 1700
    tail = "\nEnterprise SAML SSO is included in the plan. USD 42 per user, billed annually.\n"
    citation = "A separate fact relevant to the requested comparison."
    text = prefix + tail + "Filler. " * 2500 + citation
    source = {
        "id": "long",
        "title": "Pricing",
        "text": text,
        "competitor": "A",
        "url": "https://example.com/pricing",
        "retrieved_at": "2026-10-02T00:00:00Z",
        "published_at": None,
    }
    claims = [{"evidence": [{"source_id": "long", "quote": citation}]}]
    context = ResearchEngine.context_sources([source], ["价格", "SSO"], claims)[0]
    assert tail.strip() in context["text"] and citation in context["text"]
    assert context["url"] == source["url"] and context["retrieved_at"] == source["retrieved_at"]
    assert context["truncated"] and context["omitted_citations"] == 0
    assert sum(w["end"] - w["start"] for w in context["window_offsets"]) <= 12000
    for window in context["window_offsets"]:
        assert text[window["start"] : window["end"]] in context["text"]


async def test_unresolved_fact_cannot_be_supported_in_another_section(store):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "replay"})
    engine = ResearchEngine(store, run["id"])
    source = next(
        s
        for s in store.list_sources(project["id"])
        if s["competitor"] == COMPANIES[0] and s["kind"] == "release"
    )
    quote = next(line for line in source["text"].splitlines() if line.startswith("SSO："))
    claims = validate_claims(
        [
            {
                "subject": COMPANIES[0],
                "dimension": dimension,
                "statement": quote,
                "status": "supported",
                "evidence": [{"source_id": source["id"], "quote": quote, "locator": ""}],
            }
            for dimension in ["SSO", "近期变化"]
        ],
        [source],
        {COMPANIES[0]},
    )
    state = {
        **engine.initial_state(),
        "claims": claims,
        "issues": [
            {
                "subject": COMPANIES[0],
                "dimension": "SSO",
                "source_ids": [source["id"]],
                "reason": "当前套餐范围有冲突。",
                "resolution": "uncertain",
                "category": "current_conflict",
            }
        ],
    }
    output = await engine.edit(state)
    assert all(c["status"] == "uncertain" for c in store.get_report(output["report_id"])["claims"])


async def test_semantically_rejected_assertion_is_withdrawn_from_report(store):
    project = seed_demo(store)
    run = store.create_run(project["id"], {"mode": "replay"})
    engine = ResearchEngine(store, run["id"])
    source = next(s for s in store.list_sources(project["id"]) if s["competitor"] == COMPANIES[0])
    claim = validate_claims(
        [
            {
                "subject": COMPANIES[0],
                "dimension": "SSO",
                "statement": "SSO 必须额外付费",
                "status": "supported",
                "conditions": ["只有最高套餐支持"],
                "evidence": [{"source_id": source["id"], "quote": source["text"][:40], "locator": ""}],
            }
        ],
        [source],
        {COMPANIES[0]},
    )[0]
    state = {
        **engine.initial_state(),
        "claims": [claim],
        "issues": [
            {
                "subject": COMPANIES[0],
                "dimension": "SSO",
                "source_ids": [source["id"]],
                "reason": "所引段落不支持付费和套餐断言。",
                "resolution": "uncertain",
                "category": "citation",
            }
        ],
    }
    output = await engine.edit(state)
    final_claim = store.get_report(output["report_id"])["claims"][0]
    assert "额外付费" not in final_claim["statement"]
    assert "只有最高套餐支持" not in str(final_claim["conditions"])
    assert final_claim["value"] is None and final_claim["status"] == "uncertain"


def test_model_context_uses_frozen_user_time_scope(store):
    project = seed_demo(store)
    store.update_project(project["id"], {"time_range": "2026年第二季度"})
    run = store.create_run(project["id"], {"mode": "replay"})
    engine = ResearchEngine(store, run["id"])
    payload = json.loads(engine.messages("verifier", {})[-1]["content"])
    assert payload["research_scope"] == {"time_range": "2026年第二季度", "as_of": run["created_at"]}


def price_claim(statement, quote="团队版 USD 12/席位/月，按年付费，未含税；月付为 USD 16/席位/月。"):
    source = {"id": "price", "competitor": "A", "text": quote}
    claim = {
        "subject": "A",
        "dimension": "价格",
        "statement": statement,
        "status": "supported",
        "value": "USD 12/席位/月，按年付费",
        "conditions": ["按年付费", "未含税"],
        "evidence": [{"source_id": "price", "quote": quote, "locator": "价格"}],
    }
    return source, claim


@pytest.mark.parametrize("field", ["statement", "value", "conditions"])
@pytest.mark.parametrize(
    "bad",
    [
        "每年每席位12美元（按年付费）或每月每席位16美元（按月付费）",
        "团队版价格为每年支付12美元/席位，按月支付为16美元/席位。",
    ],
)
def test_same_price_monthly_rate_cannot_be_rewritten_as_annual_in_any_field(field, bad):
    source, claim = price_claim("每月每席位12美元，按年付费。")
    claim[field] = [bad] if field == "conditions" else bad
    checked = validate_claims([claim], [source], {"A"})[0]
    assert checked["status"] == "uncertain" and checked["value"] is None
    assert checked["conditions"] == [PRICE_UNIT_GUARD_REASON]
    assert "12" not in checked["statement"]
    issues, _ = _ground_review_issues([checked], [source], [])
    assert issues[0]["resolution"] == "followup" and issues[0]["category"] == "conditions"


@pytest.mark.parametrize(
    "quote,statement,mismatch",
    [
        ("USD 18 per seat per month, billed annually", "USD 18 per seat per year, billed annually", True),
        ("USD 9/seat/month, billed annually", "每席位每年9美元，按年付费", True),
        ("USD 24/user/year", "每月每用户24美元", True),
        ("AUD 12/席位/月，按年付费", "每年每席位12澳元", True),
        ("AUD 27/席位/月，按年付费", "每年支付27澳元/席位，按月支付为35澳元/席位", True),
        ("USD 12/席位/月，按年付费", "每年需支付 USD 12/seat", True),
        ("USD 12/席位/月，按年付费", "每年每席位支付12美元", True),
        ("USD 12/席位/月，按年付费", "每月每席位12美元，按年付费", False),
        ("USD 12 per user per month, billed annually", "USD 12/user/month, billed annually", False),
        ("USD 12/席位/月，按年付费", "USD 144/席位/年", False),
        ("USD 12/席位/月，按年付费", "每年支付144美元/席位", False),
        ("USD 12/席位/月，按年付费", "每年支付时每席位每月12美元", False),
        ("USD 12/席位/月，按年付费", "每年支付12美元/席位/月", False),
        ("USD 12/席位/月，按年付费", "每年支付 AUD 12/seat", False),
        ("USD 12/席位/月，按年付费", "AUD 12/席位/年", False),
        ("USD 12/席位/月，按年付费", "12美元，按年付费", False),
        ("$12 per seat per month", "USD 12 per seat per year", False),
    ],
)
def test_price_rate_guard_is_explicit_currency_aware_and_does_not_infer_conversions(
    quote, statement, mismatch
):
    source, claim = price_claim(statement, quote)
    claim["value"] = None
    checked = validate_claims([claim], [source], {"A"})[0]
    assert (checked["status"] == "uncertain") is mismatch


def new_price_project(store):
    project = store.create_project(
        {
            "title": "Pricing test",
            "question": "Compare Atlas pricing",
            "competitors": ["Atlas"],
            "dimensions": ["价格"],
            "workspace_type": "synthetic",
        }
    )
    source = store.add_source(
        project["id"],
        {
            "title": "Current pricing",
            "competitor": "Atlas",
            "kind": "pricing",
            "synthetic": True,
            "published_at": "2027-02-01",
            "text": "价格：专业版 USD 23.50/席位/月，按年付费，未含税；月付为 USD 29/席位/月。定制版需单独询价。\n计费说明：报价按有效席位计算。",
        },
    )
    return project, source


def draft_price(source, statement, status="supported"):
    return {
        "subject": "Atlas",
        "dimension": "价格",
        "statement": statement,
        "status": status,
        "value": statement,
        "conditions": [],
        "evidence": [
            {"source_id": source["id"], "quote": "计费说明：报价按有效席位计算。", "locator": "计费说明"}
        ],
    }


@pytest.mark.parametrize("architecture", ["single", "pipeline", "multi"])
@pytest.mark.parametrize("draft", ["专业版每年支付23.50美元/席位。", "月度报价和年度付款需要分别比较。"])
async def test_source_price_facts_replace_paraphrase_variance_equally(store, architecture, draft):
    project, source = new_price_project(store)
    run = store.create_run(project["id"], {"mode": "live", "architecture": architecture})
    engine = ResearchEngine(store, run["id"])

    class Provider:
        async def structured(self, messages, schema, purpose, **kwargs):
            payload = json.loads(messages[-1]["content"])
            assert payload["source_fact_candidates"][0]["source_id"] == source["id"]
            return ResearchOutput(claims=[draft_price(source, draft)])

    engine.provider = Provider()
    output = await engine.research_one(
        {
            "role": "single" if architecture == "single" else "commercial",
            "target": "Atlas",
            "dimensions": ["价格"],
        },
        engine.initial_state(),
    )
    price = output["claims"][0]
    assert price["status"] == "supported"
    assert price["statement"] == price["value"] == source["text"]
    assert "未含税" in price["statement"] and "定制版需单独询价" in price["statement"]
    assert price["computation"]["type"] == "source_price_facts"
    assert {fact["amount"] for fact in price["computation"]["facts"]} == {"23.50", "29"}
    decision = next(
        e["payload"]
        for e in store.list_events(run["id"])
        if e["type"] == "evidence_decision" and e["payload"]["action"] == "facts_normalized"
    )
    assert decision["before"]["statement"] == draft
    assert decision["task_ids"] == [store.list_tasks(run["id"])[0]["id"]]
    assert decision["after"]["evidence"][0]["quote"] == source["text"]


def test_price_compilation_never_uses_unread_or_other_company_sources(store):
    _, source = new_price_project(store)
    claim = draft_price(source, "报价待确认", "uncertain")
    assert _compile_price_claim({**claim, "evidence": []}, [source]) is None
    assert _compile_price_claim({**claim, "subject": "Other"}, [source]) is None
    alternative = {**source, "id": "different", "text": source["text"].replace("23.50", "25")}
    claim["evidence"].append({"source_id": "different", "quote": alternative["text"], "locator": ""})
    assert _compile_price_claim(claim, [source, alternative]) is None


def test_price_compilation_preserves_explicit_plan_association_ambiguity(store):
    _, source = new_price_project(store)
    claim = draft_price(source, "报价无法对应到明确套餐，表格列关联不明确。", "uncertain")
    assert _compile_price_claim(claim, [source]) is None


def test_long_price_quote_defers_instead_of_truncating_conditions(store):
    _, source = new_price_project(store)
    source["text"] = source["text"].splitlines()[0] + "\n计费说明：" + "适用条件。" * 190
    claim = {
        **draft_price(source, "报价待核查"),
        "evidence": [{"source_id": source["id"], "quote": source["text"].splitlines()[0], "locator": ""}],
    }
    assert _compile_price_claim(claim, [source]) is None


@pytest.mark.parametrize("other_date", [None, "2027-01-01", "2027-02-01"])
async def test_uncertain_price_cannot_be_resurrected_across_nonobsolete_conflicts(store, other_date):
    project, source = new_price_project(store)
    alternative = store.add_source(
        project["id"],
        {
            **source,
            "logical_key": "different-price",
            "title": "Other current pricing",
            "published_at": other_date,
            "text": source["text"].replace("23.50", "25"),
        },
    )
    run = store.create_run(project["id"], {"mode": "live", "architecture": "multi"})
    engine = ResearchEngine(store, run["id"])

    class Provider:
        async def structured(self, *args, **kwargs):
            return ResearchOutput(claims=[draft_price(source, "报价存在冲突，无法确认", "uncertain")])

    engine.provider = Provider()
    price = (
        await engine.research_one(
            {"role": "commercial", "target": "Atlas", "dimensions": ["价格"]}, engine.initial_state()
        )
    )["claims"][0]
    assert price["status"] == "uncertain" and price["value"] is None
    issues = _source_conflict_issues([price], [source, alternative])
    assert issues[0]["category"] == "current_conflict"
    assert set(issues[0]["source_ids"]) == {source["id"], alternative["id"]}


def test_critic_cannot_replace_exact_price_quote_with_generic_billing_principle(store):
    _, source = new_price_project(store)
    compiled = _compile_price_claim(draft_price(source, "一般计费原则"), [source])
    issue = {
        "subject": "Atlas",
        "dimension": "价格",
        "reason": "应该说明报价与收款周期不同，不宜直接给出报价",
        "source_ids": [source["id"]],
        "category": "conditions",
        "resolution": "followup",
    }
    checked, notices = _ground_review_issues([compiled], [source], [issue])
    assert checked == [] and notices
    alternative = {**source, "id": "other-price", "text": source["text"].replace("23.50", "25")}
    checked, _ = _ground_review_issues([compiled], [source, alternative], [])
    assert checked[0]["category"] == "current_conflict"


def test_targeted_retrieval_prioritizes_concrete_current_faq_over_nondisclosure():
    sources = [
        {
            "id": f"s{i}",
            "competitor": "Atlas",
            "kind": "security",
            "published_at": "2027-02-01",
            "text": "SSO：现有材料未披露套餐支持范围。",
        }
        for i in range(12)
    ]
    faq = {
        "id": "faq",
        "competitor": "Atlas",
        "kind": "faq",
        "published_at": "2027-02-02",
        "text": "SSO：专业版支持 SAML SSO，基础版不支持。",
    }
    assert _relevant_sources([*sources, faq], ["SSO"], limit=2)[0]["id"] == "faq"


async def test_final_consistency_propagates_sso_conflict_across_disjoint_sources(store):
    project = store.create_project(
        {
            "title": "Scope conflict",
            "question": "Compare SSO",
            "competitors": ["Atlas"],
            "dimensions": ["SSO", "近期变化"],
            "workspace_type": "synthetic",
        }
    )
    first = store.add_source(
        project["id"],
        {
            "title": "Security",
            "logical_key": "security",
            "competitor": "Atlas",
            "kind": "security",
            "synthetic": True,
            "published_at": "2027-02-01",
            "text": "SSO：专业版支持 SAML SSO，基础版不支持。",
        },
    )
    second = store.add_source(
        project["id"],
        {
            "title": "Release",
            "logical_key": "release",
            "competitor": "Atlas",
            "kind": "release",
            "synthetic": True,
            "published_at": "2027-02-01",
            "text": "SSO：基础版与专业版均支持 SAML SSO。",
        },
    )
    claims = [
        {
            "subject": "Atlas",
            "dimension": dimension,
            "statement": source["text"],
            "status": "supported",
            "value": "基础版可用",
            "conditions": ["基础版可用"],
            "evidence": [{"source_id": source["id"], "quote": source["text"], "locator": ""}],
        }
        for source, dimension in [(first, "SSO"), (second, "近期变化")]
    ]
    assert _source_conflict_issues(claims, [first, second])[0]["category"] == "current_conflict"
    run = store.create_run(project["id"], {"mode": "replay", "architecture": "single"})
    engine = ResearchEngine(store, run["id"])
    result = await engine.edit({**engine.initial_state(), "claims": claims, "issues": []})
    final = store.get_report(result["report_id"])["claims"]
    assert all(c["status"] == "uncertain" and c["value"] is None for c in final)
    assert all("基础版可用" not in str(c["conditions"]) for c in final)
    assert any(
        e["type"] == "evidence_decision" and e["payload"]["action"] == "confidence_propagated"
        for e in store.list_events(run["id"])
    )


async def test_dynamic_live_graph_resolves_numeric_gap_with_attributed_correction_events(store):
    project, source = new_price_project(store)
    run = store.create_run(project["id"], {"mode": "live", "architecture": "multi"})
    engine = ResearchEngine(store, run["id"])

    class Provider:
        tools_called = 0

        async def preflight(self):
            return {}

        async def structured(self, messages, schema, purpose, **kwargs):
            if schema is PlanOutput:
                return PlanOutput(
                    tasks=[
                        {
                            "role": "commercial",
                            "target": "Atlas",
                            "dimensions": ["价格"],
                            "question": "核对定价",
                        }
                    ]
                )
            if issubclass(schema, ResearchOutput):
                return ResearchOutput(
                    claims=[
                        {
                            "subject": "Atlas",
                            "dimension": "价格",
                            "statement": "无法确认报价",
                            "status": "uncertain",
                            "evidence": [],
                            "value": None,
                            "conditions": [],
                        }
                    ]
                )
            if issubclass(schema, ReviewOutput):
                return ReviewOutput(issues=[])
            assert schema is EditorOutput
            return EditorOutput(
                summary_keys=["Atlas::价格"], sections=[{"heading": "价格", "claim_keys": ["Atlas::价格"]}]
            )

        async def tool_turn(self, messages, definitions, purpose):
            self.tools_called += 1
            assert source["id"] in {s["id"] for s in json.loads(messages[-1]["content"])["sources"]}
            args = {"claims": [draft_price(source, "一般计费原则")]}
            return {
                "name": "submit_claims",
                "arguments": args,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tool1",
                            "type": "function",
                            "function": {"name": "submit_claims", "arguments": json.dumps(args)},
                        }
                    ],
                },
                "call_id": "tool1",
            }

    provider = Provider()
    engine.provider = provider
    report = await engine.run_graph()
    claim = next(c for c in report["claims"] if c["dimension"] == "价格")
    assert claim["status"] == "supported" and "USD 23.50" in claim["statement"]
    assert provider.tools_called == 1
    events = [e["payload"] for e in store.list_events(run["id"]) if e["type"] == "evidence_decision"]
    assert {"followup_requested", "facts_normalized", "claim_revised"}.issubset({e["action"] for e in events})
    task_ids = {t["id"] for t in store.list_tasks(run["id"])}
    assert all(set(event["task_ids"]).issubset(task_ids) for event in events)
    correction = next(e for e in events if e["action"] == "claim_revised")
    assert correction["before"]["status"] == "uncertain" and correction["after"]["status"] == "supported"


async def test_compiled_price_facts_export_with_quote_conditions_and_snapshot(store, tmp_path):
    from xml.etree import ElementTree as ET
    from zipfile import ZipFile

    from briefforge.exports import build_export_model, export_report

    project = seed_demo(store)
    _, report = await execute(store, project)
    prices = [c for c in report["claims"] if c["dimension"] == "价格"]
    assert all(c["computation"]["type"] == "source_price_facts" for c in prices)
    model = build_export_model(report)
    assert model["chart"]["kind"] == "price"
    output = export_report(report, "docx", tmp_path)
    with ZipFile(output) as archive:
        document = "".join(ET.fromstring(archive.read("word/document.xml")).itertext())
        assert all(c["statement"].replace("\n", "") in document for c in prices)
        embedded = "".join(ET.fromstring(archive.read("customXml/briefforge-snapshot.xml")).itertext())
        manifest = json.loads(embedded)
        assert all(
            c["computation"]["type"] == "source_price_facts"
            for c in manifest["claims"]
            if c["dimension"] == "价格"
        )
