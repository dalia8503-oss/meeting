from fastapi.testclient import TestClient

from meeting_action_agent.api.app import create_app

SECRET = "AIza-test-secret-value"


def _client():
    return TestClient(create_app())


def test_health_ok_and_default_mode_is_demo(monkeypatch):
    monkeypatch.delenv("MAA_MODE", raising=False)
    r = _client().get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "demo"


def test_health_live_mode(monkeypatch):
    monkeypatch.setenv("MAA_MODE", "live")
    assert _client().get("/api/health").json()["mode"] == "live"


def test_invalid_mode_falls_back_to_demo(monkeypatch):
    monkeypatch.setenv("MAA_MODE", "production")
    assert _client().get("/api/health").json()["mode"] == "demo"


def test_mode_cannot_be_changed_by_request(monkeypatch):
    monkeypatch.delenv("MAA_MODE", raising=False)
    r = _client().get("/api/health?mode=live", headers={"X-MAA-Mode": "live"})
    assert r.json()["mode"] == "demo"


def test_health_never_exposes_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    r = _client().get("/api/health")
    assert SECRET not in r.text
    assert r.json()["gemini_configured"] is True
    monkeypatch.delenv("GEMINI_API_KEY")
    assert _client().get("/api/health").json()["gemini_configured"] is False


def test_cors_allows_only_vite_dev_origin():
    c = _client()
    ok = c.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = c.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_health_reports_malformed_gemini_key_as_not_configured(monkeypatch):
    # .env 내용을 통째로 붙여 넣은 값(가짜): 한 줄 키만 정상으로 본다
    monkeypatch.setenv("GEMINI_API_KEY", "GEMINI_API_KEY=fake-key-123\nGEMINI_MODEL=m")
    assert _client().get("/api/health").json()["gemini_configured"] is False
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-123")
    assert _client().get("/api/health").json()["gemini_configured"] is True
    monkeypatch.delenv("GEMINI_API_KEY")
    assert _client().get("/api/health").json()["gemini_configured"] is False
