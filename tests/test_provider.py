import json

import httpx
import pytest

from briefforge.engine import ResearchOutput, SourceRead
from briefforge.provider import (
    ENDPOINT,
    MODEL_PROFILES,
    InvalidModelOutput,
    OpenRouterProvider,
    ProviderError,
    ProviderUnavailable,
    strict_schema,
)
from briefforge.store import BudgetExceeded, LeaseLost, Store


def endpoint(price="0.0000000875", parameters=None, tag=ENDPOINT):
    return {
        "data": {
            "endpoints": [
                {
                    "tag": tag,
                    "status": 0,
                    "pricing": {"prompt": price, "completion": "0.00000035"},
                    "supported_parameters": parameters
                    if parameters is not None
                    else ["tools", "tool_choice", "response_format", "structured_outputs", "max_tokens"],
                }
            ]
        }
    }


@pytest.fixture
def live_store(tmp_path, monkeypatch, request):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-real-secret")
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}", tmp_path)
    project = store.create_project(
        {
            "title": "test",
            "question": "test",
            "audience": "test",
            "mode": "synthetic",
            "competitors": ["A"],
            "dimensions": ["价格"],
        }
    )
    run = store.create_run(
        project["id"], {"mode": "live", "model_profile": getattr(request, "param", "qwen-default")}
    )
    return store, run


async def test_preflight_rejects_expensive_or_missing_capability_before_paid_request(live_store):
    store, run = live_store
    for data in [endpoint("0.000001"), endpoint(parameters=["tools"]), {"data": {"endpoints": []}}]:
        requests = []

        def route(request, requests=requests, data=data):
            requests.append(request.method)
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            provider = OpenRouterProvider(store, run["id"], client=client)
            with pytest.raises(ProviderUnavailable):
                await provider.structured([], ResearchOutput, "test")
        assert requests == ["GET"]
    assert store.get_run(run["id"])["request_count"] == 0


async def test_reserve_before_send_and_settle_actual_usage(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        assert store.get_run(run["id"])["request_count"] == 1
        assert store.get_run(run["id"])["reserved_usd"] > 0
        payload = json.loads(request.content)
        assert payload["provider"]["allow_fallbacks"] is False
        assert payload["provider"]["only"] == [ENDPOINT]
        assert payload["provider"]["require_parameters"] is True
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "usage": {"cost": 0.00008},
                "choices": [{"message": {"content": '{"claims": []}'}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        assert (await provider.structured([], ResearchOutput, "test")).claims == []
    result = store.get_run(run["id"])
    assert result["reserved_usd"] == 0
    assert result["spent_usd"] == 0.00008


async def test_unknown_cost_retained_even_when_invalid_json(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        with pytest.raises(InvalidModelOutput):
            await provider.structured([], ResearchOutput, "test")
    assert store.get_run(run["id"])["reserved_usd"] > 0
    records = list((store.data_dir / "model-responses" / run["id"]).glob("*.json"))
    assert len(records) == 2
    saved = json.loads(records[0].read_text(encoding="utf-8"))
    assert saved["response"]["choices"][0]["message"]["content"] == "not json"
    assert "headers" not in saved
    validation = next(
        event for event in store.list_events(run["id"]) if event["type"] == "model_validation_failed"
    )
    assert validation["payload"]["errors"]


@pytest.mark.parametrize("usage", [[], "unexpected", True, {"server_tool_use": []}])
async def test_malformed_usage_never_skips_conservative_settlement(live_store, usage):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        return httpx.Response(
            200, json={"usage": usage, "choices": [{"message": {"content": '{"claims": []}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).structured(
            [], ResearchOutput, "usage"
        )
        assert result.claims == []
    saved = store.get_run(run["id"])
    assert saved["request_count"] == 1 and saved["reserved_usd"] > 0
    assert saved["spent_usd"] == 0


@pytest.mark.parametrize("choices", [[], "unexpected", [{}], [{"message": []}]])
async def test_invalid_envelope_is_accounted_and_reported_without_crashing(live_store, choices):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        return httpx.Response(200, json={"usage": {"cost": 0.0001}, "choices": choices})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(InvalidModelOutput, match="有效内容结构"):
            await OpenRouterProvider(store, run["id"], client=client).structured(
                [], ResearchOutput, "envelope"
            )
    assert store.get_run(run["id"])["spent_usd"] == 0.0001
    assert store.get_run(run["id"])["reserved_usd"] == 0


async def test_timeout_keeps_reservation_and_never_invents_output(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        with pytest.raises(ProviderError, match="费用状态未知"):
            await provider.structured([], ResearchOutput, "test")
    assert store.get_run(run["id"])["reserved_usd"] > 0
    assert store.get_run(run["id"])["request_count"] == 1


async def test_each_retry_gets_new_reservation(live_store):
    store, run = live_store
    calls = []

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "limited"}})
        return httpx.Response(
            200, json={"usage": {"cost": 0.0001}, "choices": [{"message": {"content": '{"claims": []}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        await provider.structured([], ResearchOutput, "test")
    result = store.get_run(run["id"])
    assert result["request_count"] == 2
    assert result["reserved_usd"] > 0  # 429 has no authoritative cost.
    assert result["spent_usd"] == 0.0001


async def test_budget_refuses_before_http_post(live_store):
    store, run = live_store
    store.update_run(run["id"], {"budget_usd": 0.000001})
    methods = []

    def route(request):
        methods.append(request.method)
        return httpx.Response(200, json=endpoint())

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(BudgetExceeded):
            await OpenRouterProvider(store, run["id"], client=client).structured([], ResearchOutput, "test")
    assert methods == ["GET"]


async def test_search_caps_calls_and_accepts_only_provider_citations(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        payload = json.loads(request.content)
        assert payload["max_tool_calls"] == 1
        params = payload["tools"][0]["parameters"]
        assert params["max_uses"] == 1 and params["max_results"] == 3
        assert params["engine"] == "parallel" and params["mode"] == "basic"
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "content": "Made-up link https://unverified.example/",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://official.example/prices",
                                        "title": "Price",
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        for number in range(6):
            assert await provider.search("pricing", f"search{number}") == [
                {"url": "https://official.example/prices", "title": "Price"}
            ]
        with pytest.raises(ProviderError, match="六次"):
            await provider.search("pricing", "seventh")
    assert store.get_run(run["id"])["request_count"] == 6


def test_strict_schema_forbids_additional_fields():
    schema = strict_schema(ResearchOutput)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    claim = schema["properties"]["claims"]["items"]
    evidence = claim["properties"]["evidence"]["items"]
    for definition in [schema, claim, evidence]:
        assert definition["type"] == "object"
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])


async def test_gemini_string_claim_regression_inlines_nested_tool_schema_and_repairs_once(live_store):
    store, run = live_store
    calls = []
    reasoning = [{"type": "reasoning.encrypted", "data": "opaque-signature"}]
    valid = {
        "claims": [
            {
                "subject": "A",
                "dimension": "价格",
                "statement": "Price: USD 12",
                "status": "supported",
                "evidence": [{"source_id": "source-1", "quote": "Price: USD 12", "locator": "price"}],
                "value": "USD 12",
                "conditions": [],
            }
        ]
    }

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        payload = json.loads(request.content)
        calls.append(payload)
        function = payload["tools"][0]["function"]
        assert function["parameters"]["properties"]["claims"]["items"]["type"] == "object"
        assert (
            function["parameters"]["properties"]["claims"]["items"]["properties"]["evidence"]["items"]["type"]
            == "object"
        )
        assert "$ref" not in json.dumps(function["parameters"])
        if len(calls) == 1:
            arguments = {"claims": ["Price: USD 12 (source-1)"]}
        else:
            assert len(payload["tools"]) == 1 and function["name"] == "submit_claims"
            assert payload["messages"][-2]["reasoning_details"] == reasoning
            assert payload["messages"][-1]["role"] == "tool"
            arguments = valid
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "reasoning_details": reasoning,
                            "tool_calls": [
                                {
                                    "id": f"call-{len(calls)}",
                                    "type": "function",
                                    "function": {"name": "submit_claims", "arguments": json.dumps(arguments)},
                                }
                            ],
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).tool_turn(
            [], {"submit_claims": ("Submit evidenced objects", ResearchOutput)}, "shape-regression"
        )
    assert result["arguments"] == valid
    assert len(calls) == 2
    assert store.get_run(run["id"])["request_count"] == 2
    assert store.get_run(run["id"])["spent_usd"] == 0.0002
    assert any(event["type"] == "tool_schema_repair" for event in store.list_events(run["id"]))


@pytest.mark.parametrize("malformed", [True, False])
async def test_argument_repair_never_replays_invalid_json_but_preserves_valid_history(live_store, malformed):
    store, run = live_store
    initial_arguments = (
        '{"source_ids":["source-missing-brace"]'
        if malformed
        else ' { "source_ids": ["1", "2", "3", "4", "5", "6", "7"] } '
    )
    reasoning = [{"type": "reasoning.encrypted", "data": "opaque-signature"}]
    attempts = []

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        payload = json.loads(request.content)
        attempts.append(payload)
        if len(attempts) == 2:
            assert [tool["function"]["name"] for tool in payload["tools"]] == ["read_sources"]
            if malformed:
                assert payload["messages"][-1]["role"] == "system"
                assert not any(message.get("tool_calls") for message in payload["messages"])
                assert "source-missing-brace" not in json.dumps(payload)
            else:
                history = payload["messages"][-2]
                assert history["tool_calls"][0]["function"]["arguments"] == initial_arguments
                assert history["reasoning_details"] == reasoning
                assert payload["messages"][-1]["role"] == "tool"
        arguments = initial_arguments if len(attempts) == 1 else '{"source_ids":["1"]}'
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "reasoning_details": reasoning,
                            "tool_calls": [
                                {
                                    "id": f"call-{len(attempts)}",
                                    "type": "function",
                                    "function": {"name": "read_sources", "arguments": arguments},
                                }
                            ],
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).tool_turn(
            [], {"read_sources": ("Read", SourceRead)}, "history-json"
        )
    assert result["arguments"] == {"source_ids": ["1"]}
    assert len(attempts) == store.get_run(run["id"])["request_count"] == 2
    assert store.get_run(run["id"])["spent_usd"] == 0.0002
    saved = [
        json.loads(path.read_text(encoding="utf8"))
        for path in (store.data_dir / "model-responses" / run["id"]).glob("*.json")
    ]
    original = next(response for response in saved if response["purpose"] == "history-json")
    assert (
        original["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        == initial_arguments
    )
    repair = next(event for event in store.list_events(run["id"]) if event["type"] == "tool_schema_repair")
    assert repair["payload"]["malformed_history_omitted"] is malformed


@pytest.mark.parametrize(
    "name,arguments",
    [("shell", '{"command":"dir"}'), ("read_sources", '{"source_ids": ["1","2","3","4","5","6","7"]}')],
)
async def test_tool_allowlist_and_arguments_validated_before_dispatch(live_store, name, arguments):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": name, "arguments": arguments},
                                }
                            ]
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        with pytest.raises(InvalidModelOutput):
            await provider.tool_turn([], {"read_sources": ("Read", SourceRead)}, "bad-tool")


async def test_successful_tool_call_uses_bounded_validated_schema(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        payload = json.loads(request.content)
        assert payload["tool_choice"] == "required"
        assert payload["tools"][0]["function"]["parameters"]["properties"]["source_ids"]["maxItems"] == 6
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_sources",
                                        "arguments": '{"source_ids":["source-1"]}',
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).tool_turn(
            [], {"read_sources": ("Read", SourceRead)}, "read"
        )
    assert result["arguments"] == {"source_ids": ["source-1"]}
    assert result["call_id"] == "call-1"


def read_call(call_id, source_ids):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "read_sources", "arguments": json.dumps({"source_ids": source_ids})},
    }


@pytest.mark.parametrize("live_store", ["gemini-budget"], indirect=True)
async def test_gemini_two_read_calls_are_repaired_to_one_bounded_call(live_store):
    store, run = live_store
    requests = []

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint(tag="google-ai-studio"))
        payload = json.loads(request.content)
        requests.append(payload)
        assert "parallel_tool_calls" not in payload
        assert store.get_run(run["id"])["request_count"] == len(requests)
        if len(requests) == 1:
            calls = [read_call("current", ["new-1", "new-2"]), read_call("archive", ["old"])]
        else:
            assert payload["messages"][-1]["role"] == "system"
            assert "合并到一次 read_sources" in payload["messages"][-1]["content"]
            assert not any(message.get("tool_calls") for message in payload["messages"])
            calls = [read_call("merged", ["new-1", "new-2", "old"])]
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None, "tool_calls": calls},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).tool_turn(
            [], {"read_sources": ("Read", SourceRead)}, "two-reads"
        )
    assert result["call_id"] == "merged"
    assert result["arguments"] == {"source_ids": ["new-1", "new-2", "old"]}
    assert len(requests) == 2 and store.get_run(run["id"])["spent_usd"] == 0.0002
    assert len(list((store.data_dir / "model-responses" / run["id"]).glob("*.json"))) == 2
    assert sum(event["type"] == "tool_selection_repair" for event in store.list_events(run["id"])) == 1


@pytest.mark.parametrize(
    "initial_calls",
    [
        [],
        [{"id": "bad", "type": "function", "function": {"name": "shell", "arguments": "{}"}}],
        [{"type": "function", "function": {"name": "read_sources", "arguments": '{"source_ids":["1"]}'}}],
    ],
)
async def test_missing_or_unknown_tool_selection_has_one_repair(live_store, initial_calls):
    store, run = live_store
    attempts = 0

    def route(request):
        nonlocal attempts
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        attempts += 1
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "tool_calls": initial_calls if attempts == 1 else [read_call("fixed", ["1"])]
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).tool_turn(
            [], {"read_sources": ("Read", SourceRead)}, "selection"
        )
    assert result["call_id"] == "fixed" and attempts == 2


@pytest.mark.parametrize("arguments_first", [False, True])
async def test_tool_selection_and_argument_errors_share_one_repair_allowance(live_store, arguments_first):
    store, run = live_store
    attempts = 0

    def route(request):
        nonlocal attempts
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        attempts += 1
        bad_arguments = (attempts == 1) == arguments_first
        calls = (
            [read_call("too-many-sources", list("1234567"))]
            if bad_arguments
            else [read_call("one", ["1"]), read_call("two", ["2"])]
        )
        return httpx.Response(
            200, json={"usage": {"cost": 0.0001}, "choices": [{"message": {"tool_calls": calls}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(InvalidModelOutput):
            await OpenRouterProvider(store, run["id"], client=client).tool_turn(
                [], {"read_sources": ("Read", SourceRead)}, "shared-repair"
            )
    assert attempts == 2
    assert store.get_run(run["id"])["request_count"] == 2
    assert store.get_run(run["id"])["spent_usd"] == 0.0002


async def test_schema_repair_is_bounded_and_each_attempt_billed(live_store):
    store, run = live_store
    attempts = []

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        payload = json.loads(request.content)
        attempts.append(payload)
        content = '{"wrong_field": []}' if len(attempts) == 1 else '{"claims": []}'
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        result = await OpenRouterProvider(store, run["id"], client=client).structured(
            [], ResearchOutput, "repair"
        )
    assert result.claims == []
    assert len(attempts) == 2
    assert store.get_run(run["id"])["request_count"] == 2
    assert store.get_run(run["id"])["spent_usd"] == 0.0002
    assert any(event["type"] == "model_validation_failed" for event in store.list_events(run["id"]))


async def test_known_charge_settled_before_lost_lease_rejects_diagnostic_event(live_store, monkeypatch):
    store, run = live_store
    original = store.add_event

    def event(run_id, type, message, payload=None):
        if type == "model_response_saved":
            raise LeaseLost("ownership moved during HTTP")
        return original(run_id, type, message, payload)

    monkeypatch.setattr(store, "add_event", event)

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint())
        return httpx.Response(
            200, json={"usage": {"cost": 0.0001}, "choices": [{"message": {"content": '{"claims": []}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(LeaseLost):
            await OpenRouterProvider(store, run["id"], client=client).structured(
                [], ResearchOutput, "late-response"
            )
    assert store.get_run(run["id"])["spent_usd"] == 0.0001
    assert store.get_run(run["id"])["reserved_usd"] == 0


@pytest.mark.parametrize("live_store", ["qwen-default", "gemini-budget"], indirect=True)
async def test_explicit_profile_routes_and_records_exact_model_without_fallback(live_store, monkeypatch):
    store, run = live_store
    profile_name = run["model_profile"]
    selected = MODEL_PROFILES[profile_name]
    requests = []
    monkeypatch.setenv("OPENROUTER_MODEL", "irrelevant/environment-model")

    def route(request):
        requests.append(request)
        if request.method == "GET":
            assert str(request.url).endswith(f"/models/{selected['model']}/endpoints")
            return httpx.Response(200, json=endpoint("0.0000001", tag=selected["endpoint"]))
        payload = json.loads(request.content)
        assert payload["model"] == selected["model"]
        assert payload["provider"]["only"] == [selected["endpoint"]]
        assert payload["provider"]["order"] == [selected["endpoint"]]
        assert payload["provider"]["allow_fallbacks"] is False
        assert payload["provider"]["max_price"] == {"prompt": 0.10, "completion": 0.40}
        if profile_name == "gemini-budget":
            assert "quantizations" not in payload["provider"]
        else:
            assert payload["provider"]["quantizations"] == ["fp8"]
        return httpx.Response(
            200, json={"usage": {"cost": 0.0001}, "choices": [{"message": {"content": '{"claims": []}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        await provider.structured([], ResearchOutput, "profile-smoke")
    assert [request.method for request in requests] == ["GET", "POST"]
    saved = json.loads(
        next((store.data_dir / "model-responses" / run["id"]).glob("*.json")).read_text(encoding="utf-8")
    )
    assert saved["model_profile"] == profile_name
    assert saved["model"] == selected["model"]
    assert saved["endpoint"] == selected["endpoint"]


@pytest.mark.parametrize("live_store", ["qwen-default", "gemini-budget"], indirect=True)
async def test_other_profile_endpoint_never_acts_as_automatic_fallback(live_store):
    store, run = live_store
    other = MODEL_PROFILES["gemini-budget" if run["model_profile"] == "qwen-default" else "qwen-default"]
    methods = []

    def route(request):
        methods.append(request.method)
        return httpx.Response(200, json=endpoint(tag=other["endpoint"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        with pytest.raises(ProviderUnavailable):
            await provider.structured([], ResearchOutput, "no-fallback")
    assert methods == ["GET"]
    assert store.get_run(run["id"])["request_count"] == 0


def test_legacy_run_without_profile_defaults_to_qwen(live_store, monkeypatch):
    store, run = live_store
    legacy = {key: value for key, value in run.items() if key != "model_profile"}
    monkeypatch.setattr(store, "get_run", lambda run_id: legacy)
    provider = OpenRouterProvider(store, run["id"])
    assert provider.model_profile == "qwen-default"
    assert provider.model == MODEL_PROFILES["qwen-default"]["model"]


@pytest.mark.parametrize("live_store", ["gemini-budget"], indirect=True)
async def test_gemini_serving_grammar_avoids_state_explosion_but_local_bounds_remain(live_store):
    store, run = live_store
    attempts = []

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint("0.0000001", tag="google-ai-studio"))
        payload = json.loads(request.content)
        attempts.append(payload)
        schema = payload["tools"][0]["function"]["parameters"]
        encoded = json.dumps(schema)
        assert all(key not in encoded for key in ['"maxItems"', '"maxLength"', '"minLength"', '"$ref"'])
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert schema["properties"]["source_ids"]["items"]["type"] == "string"
        assert '"maxItems": 6' in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "usage": {"cost": 0.0001},
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": f"call-{len(attempts)}",
                                    "type": "function",
                                    "function": {
                                        "name": "read_sources",
                                        "arguments": json.dumps(
                                            {"source_ids": [str(number) for number in range(7)]}
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        provider = OpenRouterProvider(store, run["id"], client=client)
        full_schema = strict_schema(ResearchOutput)
        wire = provider._wire_schema(full_schema)
        claim = wire["properties"]["claims"]["items"]
        assert claim["type"] == "object"
        assert claim["properties"]["evidence"]["items"]["type"] == "object"
        assert claim["properties"]["status"]["enum"] == ["supported", "uncertain", "contradicted"]
        assert full_schema["properties"]["claims"]["maxItems"] == 32
        with pytest.raises(InvalidModelOutput):
            await provider.tool_turn(
                [], {"read_sources": ("Read frozen evidence", SourceRead)}, "grammar-regression"
            )
    assert len(attempts) == 2
    assert store.get_run(run["id"])["request_count"] == 2


@pytest.mark.parametrize("live_store", ["gemini-budget"], indirect=True)
async def test_gemini_structured_output_uses_compact_wire_contract(live_store):
    store, run = live_store

    def route(request):
        if request.method == "GET":
            return httpx.Response(200, json=endpoint("0.0000001", tag="google-ai-studio"))
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert "maxItems" not in json.dumps(schema)
        assert schema["properties"]["claims"]["items"]["type"] == "object"
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200, json={"usage": {"cost": 0.0001}, "choices": [{"message": {"content": '{"claims": []}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        assert (
            await OpenRouterProvider(store, run["id"], client=client).structured(
                [], ResearchOutput, "compact-output"
            )
        ).claims == []
