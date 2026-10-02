import pytest
from fastapi.testclient import TestClient

from briefforge.api import create_app
from briefforge.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(f"sqlite:///{(tmp_path / 'api.db').as_posix()}", tmp_path)
    with TestClient(create_app(store)) as client:
        yield client


def make_project(client, mode="synthetic"):
    return client.post(
        "/api/projects",
        json={"title": "比较研究", "question": "比较产品价格与套餐", "competitors": ["A"], "mode": mode},
    ).json()


def test_upload_start_cancel_and_sse_reconnect(client):
    p = make_project(client)
    assert (
        client.post(
            f"/api/projects/{p['id']}/upload", files={"file": ("a.csv", b"price,USD\n10,monthly")}
        ).status_code
        == 201
    )
    response = client.post(
        f"/api/projects/{p['id']}/runs", json={"mode": "replay"}, headers={"Idempotency-Key": "one"}
    )
    assert response.status_code == 202
    run = response.json()
    assert (
        client.post(
            f"/api/projects/{p['id']}/runs", json={"mode": "replay"}, headers={"Idempotency-Key": "one"}
        ).json()["id"]
        == run["id"]
    )
    assert client.patch(f"/api/projects/{p['id']}", json={"title": "new"}).status_code == 409
    client.post(f"/api/runs/{run['id']}/cancel")
    events = client.get(f"/api/runs/{run['id']}/event-log").json()
    after = events[0]["id"]
    body = client.get(f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(after)}).text
    assert f"id: {after}\n" not in body
    assert "event: done" in body and "cancelled" in body


def test_validation_and_cross_site_rejection(client):
    assert client.post("/api/projects", json={"title": "x", "question": "too short"}).status_code == 422
    assert client.post("/api/demo", headers={"Origin": "https://evil.test"}).status_code == 403
    p = make_project(client)
    assert client.patch(f"/api/projects/{p['id']}", json={"competitors": ["A", "A"]}).status_code == 422
    assert client.get("/api/projects/nonexistent").status_code == 404


def test_public_replay_rejected(client):
    p = make_project(client, "public")
    assert client.post(f"/api/projects/{p['id']}/runs", json={"mode": "replay"}).status_code == 422
    assert "OPENROUTER_API_KEY" not in str(client.get("/api/health").json().values())


def test_revision_keeps_explicit_model_profile(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-no-provider-call")
    p = make_project(client)
    client.post(f"/api/projects/{p['id']}/sources", json={"title": "资料", "text": "有资料的测试运行"})
    run = client.post(
        f"/api/projects/{p['id']}/runs",
        json={"mode": "live", "model_profile": "gemini-budget"},
    ).json()
    store = client.app.state.store
    report = store.save_report(p["id"], run["id"], {})
    store.update_run(run["id"], {"status": "completed"})
    revised = client.post(f"/api/reports/{report['id']}/revise", json={"instructions": "补充比较套餐限制"})
    assert revised.status_code == 202
    assert report["model_profile"] == revised.json()["model_profile"] == "gemini-budget"
    assert revised.json()["mode"] == "live"


def test_uploaded_only_public_research_and_time_range(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-no-provider-call")
    p = client.post(
        "/api/projects",
        json={
            "title": "资料研究",
            "question": "只分析上传的公开资料",
            "competitors": ["A"],
            "mode": "public",
            "web_enabled": False,
            "time_range": "2026年第三季度",
        },
    ).json()
    assert not p["web_enabled"]
    assert client.post(f"/api/projects/{p['id']}/runs", json={"mode": "live"}).status_code == 422
    client.post(f"/api/projects/{p['id']}/sources", json={"title": "资料", "text": "团队版按月付费"})
    run = client.post(f"/api/projects/{p['id']}/runs", json={"mode": "live"}).json()
    assert run["brief"]["web_enabled"] is False
    assert run["brief"]["time_range"] == "2026年第三季度"
    assert run["budget_usd"] == 0.15


def test_new_run_default_and_collaboration_are_explicit(client):
    project = client.post("/api/demo").json()
    run = client.post(f"/api/projects/{project['id']}/runs", json={"mode": "replay"}).json()
    assert run["model_profile"] == "gemini-budget"
    summary = client.get(f"/api/runs/{run['id']}/collaboration").json()
    assert summary["run_id"] == run["id"]
    assert summary["mode"] == "replay"
    assert summary["metrics"]["request_count"] == 0
    assert not summary["history_available"]
    assert client.get("/api/runs/missing/collaboration").status_code == 404
