"""POST /api/meetings/{id}/analyze — 외부 API 없이 DEMO와 monkeypatch된 LIVE 의존성으로 검증한다."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_action_agent.api import service
from meeting_action_agent.api.app import create_app
from meeting_action_agent.stt.gemini_stt import GeminiSttProvider

WAV = Path(__file__).resolve().parents[1] / "fixtures" / "stt" / "tts_meeting_001.wav"


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("MAA_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "runtime"


@pytest.fixture
def client(base):
    return TestClient(create_app())


def upload(client, date="2026-11-10"):
    r = client.post("/api/meetings", data={"title": "주간", "meeting_date": date},
                    files={"audio_file": ("m.wav", WAV.read_bytes(), "audio/wav")})
    assert r.status_code == 201
    return r.json()["meeting_id"]


def test_demo_analyze_returns_actions_without_external_call(client, base):
    mid = upload(client)
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["meeting_id"], b["status"], b["mode"], b["utterance_count"]) == (mid, "analyzed", "demo", 8)
    assert len(b["actions"]) == 1 and b["rejected"] == []
    a = b["actions"][0]
    assert (a["action_id"], a["status"], a["assignee"], a["due_date"], a["due_date_normalized"]) == \
        ("A-001", "신규", "김대리", "금요일까지", "2026-11-13")
    assert a["evidence"] == {"timestamp": "00:00:12", "speaker": "팀장", "speaker_name": None, "text": "김대리, 금요일까지 도면 수정해서 공유해주세요."}
    assert b["open_issues"] and b["needs_confirmation"] and b["summary"]


def test_artifacts_are_persisted_and_original_audio_untouched(client, base):
    mid = upload(client)
    d = base / "demo"
    before = (d / "audio" / f"{mid}.wav").read_bytes()
    client.post(f"/api/meetings/{mid}/analyze")
    assert (d / "audio" / f"{mid}.wav").read_bytes() == before == WAV.read_bytes()
    assert (d / "transcripts" / "original" / f"{mid}.json").is_file()
    assert (d / "meetings" / f"{mid}.analysis.json").is_file()
    assert json.loads((d / "meetings" / f"{mid}.review.json").read_text(encoding="utf-8"))["rejected"] == []
    assert len(json.loads((d / "actions" / "actions.json").read_text(encoding="utf-8"))) == 1


def test_get_result_matches_analyze_response(client):
    mid = upload(client)
    first = client.post(f"/api/meetings/{mid}/analyze").json()
    assert client.get(f"/api/meetings/{mid}/result").json() == first


def test_second_analyze_is_409_and_does_not_duplicate(client, base):
    mid = upload(client)
    client.post(f"/api/meetings/{mid}/analyze")
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_analyzed"
    assert len(json.loads((base / "demo" / "actions" / "actions.json").read_text(encoding="utf-8"))) == 1


def test_unknown_and_unsafe_ids_are_404(client):
    for mid in ("nope", "a..b", "x y"):
        r = client.post(f"/api/meetings/{mid}/analyze")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "meeting_not_found"


def test_result_before_analysis_is_404(client):
    mid = upload(client)
    r = client.get(f"/api/meetings/{mid}/result")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_analyzed"


def test_request_cannot_select_mode_or_provider(client, base):
    mid = upload(client)
    r = client.post(f"/api/meetings/{mid}/analyze?mode=live&provider=x", json={"mode": "live", "model": "x"})
    assert r.status_code == 200 and r.json()["mode"] == "demo"


def test_live_without_key_is_503_and_creates_nothing(client, base, monkeypatch):
    monkeypatch.setenv("MAA_MODE", "live")
    mid = upload(client)
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "gemini_not_configured"
    assert not (base / "live" / "transcripts").exists()
    assert not (base / "live" / "meetings" / f"{mid}.analysis.json").exists()


def _live_deps(sample, analyst, reviewer):
    return lambda: dict(provider=GeminiSttProvider(call=lambda *a: sample), analyst_runner=analyst,
                        reviewer_runner=reviewer, speaker_map=None)


def test_live_path_same_response_schema_and_reject_is_not_an_action(client, base, monkeypatch):
    monkeypatch.setenv("MAA_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    mid = upload(client)
    from meeting_action_agent.api import demo
    sample = (Path(demo.__file__).parent / "demo_data" / "stt_response.json").read_text(encoding="utf-8")
    reject = lambda p: {"target": {"type": "action", "ref": "00:00:12"}, "verdict": "REJECT", "reasons": [{"code": "지시아님", "detail": "x"}], "attempt": 0}
    monkeypatch.setattr(service, "_live_dependencies", _live_deps(sample, demo.make_demo_analyst(mid), reject))
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "live" and b["actions"] == [] and len(b["rejected"]) == 1
    assert (base / "live" / "transcripts" / "original" / f"{mid}.json").is_file()


def test_stt_failure_is_502_without_internal_detail(client, monkeypatch):
    monkeypatch.setenv("MAA_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    mid = upload(client)

    def boom(*a):
        raise service.SttError("secret internal detail")

    from meeting_action_agent.api import demo
    monkeypatch.setattr(service, "_live_dependencies", lambda: dict(
        provider=GeminiSttProvider(call=boom), analyst_runner=demo.make_demo_analyst(mid),
        reviewer_runner=demo.demo_reviewer, speaker_map=None))
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "stt_failed"
    assert "secret" not in r.text
