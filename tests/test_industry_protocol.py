"""Task-bound model identity must be repaired or fail, never silently discarded."""

import json

import httpx
import pytest
from pydantic import ValidationError

from briefforge.engine import ResearchEngine, _bound_output_schema
from briefforge.provider import InvalidModelOutput, OpenRouterProvider, strict_schema
from briefforge.store import Store


@pytest.fixture
def industry_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}", tmp_path)
    project = store.create_project(
        {
            "title": "Interview study",
            "question": "Compare Atlas",
            "competitors": ["Atlas"],
            "dimensions": ["定位"],
            "mode": "synthetic",
        }
    )
    source = store.add_source(
        project["id"],
        {
            "title": "Interview context",
            "competitor": "",
            "kind": "industry",
            "synthetic": True,
            "published_at": "2027-02-01",
            "text": "行业背景：合成访谈中的小型设计团队需要按客户项目整理文档并追溯材料来源。\n研究边界：仅为虚构访谈情境，不代表真实市场规模或统计调查。",
        },
    )
    run = store.create_run(
        project["id"], {"mode": "live", "architecture": "multi", "model_profile": "gemini-budget"}
    )
    return store, run, source


def industry_claim(source, subject="行业"):
    return {
        "subject": subject,
        "dimension": "行业背景",
        "statement": source["text"].splitlines()[0],
        "status": "supported",
        "value": None,
        "conditions": [source["text"].splitlines()[1]],
        "evidence": [
            {"source_id": source["id"], "quote": source["text"].splitlines()[0], "locator": "行业背景"}
        ],
    }


def endpoint_response():
    return httpx.Response(
        200,
        json={
            "data": {
                "endpoints": [
                    {
                        "tag": "google-ai-studio",
                        "status": 0,
                        "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
                        "supported_parameters": [
                            "tools",
                            "tool_choice",
                            "response_format",
                            "structured_outputs",
                            "max_tokens",
                        ],
                    }
                ]
            }
        },
    )


def model_response(content, sequence):
    return httpx.Response(
        200,
        json={
            "id": f"offline-{sequence}",
            "usage": {"cost": 0.00001},
            "choices": [
                {"message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}}
            ],
        },
    )


@pytest.mark.parametrize("wrong_subject", ["知识工具行业", "行业背景", "Atlas"])
def test_task_schema_rejects_industry_aliases_and_other_company_without_coercion(
    industry_store, wrong_subject
):
    _, _, source = industry_store
    schema = _bound_output_schema({("行业", "行业背景")})
    with pytest.raises(ValidationError):
        schema.model_validate({"claims": [industry_claim(source, wrong_subject)]})
    result = schema.model_validate({"claims": [industry_claim(source)]})
    assert result.claims[0].statement == source["text"].splitlines()[0]
    assert "虚构访谈" in result.claims[0].conditions[0]
    assert '"行业"' in json.dumps(strict_schema(schema), ensure_ascii=False)


def test_bound_schema_rejects_missing_duplicate_and_cross_product_slots(industry_store):
    _, _, source = industry_store
    schema = _bound_output_schema({("行业", "行业背景"), ("Atlas", "定位")})
    with pytest.raises(ValidationError):
        schema.model_validate({"claims": []})
    with pytest.raises(ValidationError):
        schema.model_validate({"claims": [industry_claim(source), industry_claim(source)]})
    review = _bound_output_schema({("行业", "行业背景"), ("Atlas", "定位")}, review=True)
    with pytest.raises(ValidationError):
        review.model_validate(
            {
                "issues": [
                    {
                        "subject": "Atlas",
                        "dimension": "行业背景",
                        "reason": "wrong pair",
                        "source_ids": [],
                        "resolution": "followup",
                    }
                ]
            }
        )
    assert review.model_validate({"issues": []}).issues == []


async def test_wrong_industry_subject_uses_existing_one_repair_and_keeps_qualified_answer(industry_store):
    store, run, source = industry_store
    requests = []

    def route(request):
        if request.method == "GET":
            return endpoint_response()
        payload = json.loads(request.content)
        requests.append(payload)
        assert store.get_run(run["id"])["request_count"] == len(requests)
        assert store.get_run(run["id"])["reserved_usd"] > 0
        if len(requests) == 1:
            assert "claim_slots" in payload["messages"][-1]["content"]
            assert "合成访谈" in payload["messages"][-1]["content"]
        else:
            assert "subject" in payload["messages"][-1]["content"]
        return model_response(
            {"claims": [industry_claim(source, "知识工具行业" if len(requests) == 1 else "行业")]},
            len(requests),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        engine = ResearchEngine(store, run["id"], provider=provider)
        result = await engine.research_one(
            {"role": "industry", "target": "行业", "dimensions": ["行业背景"]}, engine.initial_state()
        )
    assert len(requests) == 2
    claim = result["claims"][0]
    assert claim["status"] == "supported" and claim["evidence"]
    assert claim["statement"] == source["text"].splitlines()[0]
    assert "虚构访谈" in claim["conditions"][0]
    assert store.get_run(run["id"])["spent_usd"] == 0.00002
    assert sum(e["type"] == "schema_repair" for e in store.list_events(run["id"])) == 1


async def test_repeated_wrong_company_fails_task_after_one_repair_instead_of_unknown_success(industry_store):
    store, run, source = industry_store
    requests = []

    def route(request):
        if request.method == "GET":
            return endpoint_response()
        requests.append(request)
        return model_response({"claims": [industry_claim(source, "Atlas")]}, len(requests))

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        engine = ResearchEngine(
            store, run["id"], provider=OpenRouterProvider(store, run["id"], client=client)
        )
        with pytest.raises(InvalidModelOutput):
            await engine.research_one(
                {"role": "industry", "target": "行业", "dimensions": ["行业背景"]}, engine.initial_state()
            )
    assert len(requests) == 2
    task = store.list_tasks(run["id"])[0]
    assert task["status"] == "failed"
    assert not any(e["type"] == "task_completed" for e in store.list_events(run["id"]))
    assert store.get_run(run["id"])["spent_usd"] == 0.00002


async def test_verifier_wrong_subject_repairs_instead_of_losing_industry_followup(industry_store):
    store, run, source = industry_store
    count = 0

    def route(request):
        nonlocal count
        if request.method == "GET":
            return endpoint_response()
        count += 1
        return model_response(
            {
                "issues": [
                    {
                        "subject": "行业背景" if count == 1 else "行业",
                        "dimension": "行业背景",
                        "reason": "需要保留访谈覆盖范围",
                        "source_ids": [source["id"]],
                        "resolution": "followup",
                        "category": "conditions",
                    }
                ]
            },
            count,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        engine = ResearchEngine(
            store, run["id"], provider=OpenRouterProvider(store, run["id"], client=client)
        )
        state = {**engine.initial_state(), "claims": [industry_claim(source)]}
        result = await engine.verify(state)
    assert count == 2
    assert result["issues"][0]["subject"] == "行业" and result["issues"][0]["resolution"] == "followup"


async def test_industry_followup_reads_global_sources_and_attributes_real_industry_task(industry_store):
    store, run, source = industry_store

    class Provider:
        calls = 0

        async def tool_turn(self, messages, definitions, purpose):
            self.calls += 1
            payload = json.loads(messages[-1]["content"])
            assert payload["subjects"] == ["行业"]
            assert payload["sources"][0]["id"] == source["id"]
            assert payload["sources"][0]["text"] == source["text"]
            schema = definitions["submit_claims"][1]
            with pytest.raises(ValidationError):
                schema.model_validate({"claims": [industry_claim(source, "Atlas")]})
            arguments = schema.model_validate({"claims": [industry_claim(source)]}).model_dump()
            return {"name": "submit_claims", "arguments": arguments}

    provider = Provider()
    engine = ResearchEngine(store, run["id"], provider=provider)
    unknown = {**industry_claim(source), "status": "uncertain", "statement": "无法确认", "evidence": []}
    state = {
        **engine.initial_state(),
        "claims": [unknown],
        "issues": [
            {
                "subject": "行业",
                "dimension": "行业背景",
                "reason": "读取原始访谈",
                "source_ids": [source["id"]],
                "resolution": "followup",
                "category": "missing_evidence",
            }
        ],
    }
    result = await engine.followup(state)
    assert provider.calls == 1
    assert result["claims"][0]["statement"] == source["text"].splitlines()[0]
    tasks = store.list_tasks(run["id"])
    industry_task = next(t for t in tasks if t["role"] == "industry")
    assert industry_task["round"] == 1 and not any(t["role"] == "competitor" for t in tasks)
    decision = next(
        e["payload"]
        for e in store.list_events(run["id"])
        if e["type"] == "evidence_decision" and e["payload"]["action"] == "claim_revised"
    )
    assert decision["task_ids"] == [industry_task["id"]]
