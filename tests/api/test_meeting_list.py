"""GET /api/meetings — 이전 회의 후보 목록. 저장된 파일에서 읽을 수 있는 값만 반환한다 (Gemini·Claude 호출 없음)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_action_agent.api.app import create_app

WAV = Path(__file__).resolve().parents[1] / "fixtures" / "stt" / "tts_meeting_001.wav"


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("MAA_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "runtime" / "demo"


@pytest.fixture
def client(base):
    return TestClient(create_app())


def upload(client, date, title="주간", payload=None):
    r = client.post("/api/meetings", data={"title": title, "meeting_date": date},
                    files={"audio_file": ("m.wav", payload or WAV.read_bytes(), "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()["meeting_id"]


def listing(client):
    r = client.get("/api/meetings")
    assert r.status_code == 200
    return r.json()["meetings"]


def test_empty(client):
    assert listing(client) == []


def test_single_uploaded_meeting_metadata(client):
    mid = upload(client, "2026-11-10", title="1차 회의")
    (m,) = listing(client)
    assert m["meeting_id"] == mid and m["title"] == "1차 회의" and m["meeting_date"] == "2026-11-10"
    assert m["analysis_status"] == "uploaded" and m["action_count"] == 0
    assert m["created_at"]  # audio.json recorded_at
    assert set(m) == {"meeting_id", "title", "meeting_date", "created_at", "analysis_status", "action_count"}


def test_multiple_sorted_latest_first(client):
    a = upload(client, "2026-11-10")
    c = upload(client, "2026-11-17")
    b = upload(client, "2026-11-12", payload=WAV.read_bytes() + b"\x00")  # 다른 해시
    dates = [m["meeting_date"] for m in listing(client)]
    assert dates == ["2026-11-17", "2026-11-12", "2026-11-10"]


def test_analyzed_meeting_reports_status_and_action_count(client, base):
    mid = upload(client, "2026-11-10")
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200
    expected = len(r.json()["new_actions"])
    (m,) = listing(client)
    assert m["analysis_status"] == "analyzed"
    assert m["action_count"] == expected and expected >= 1
    stored = json.loads((base / "actions" / "actions.json").read_text(encoding="utf-8"))
    assert expected == sum(1 for a in stored if a["meeting_id"] == mid)


def test_uploaded_only_meeting_has_zero_actions_next_to_analyzed(client):
    first = upload(client, "2026-11-10")
    client.post(f"/api/meetings/{first}/analyze")
    second = upload(client, "2026-11-17")
    by_id = {m["meeting_id"]: m for m in listing(client)}
    assert by_id[first]["action_count"] >= 1 and by_id[second]["action_count"] == 0
    assert by_id[second]["analysis_status"] == "uploaded"


def test_corrupt_meta_is_skipped(client, base):
    mid = upload(client, "2026-11-10")
    (base / "meetings" / "broken.meta.json").write_text("{not json", encoding="utf-8")
    assert [m["meeting_id"] for m in listing(client)] == [mid]


def test_existing_endpoints_unaffected(client):
    assert client.get("/api/health").status_code == 200
    mid = upload(client, "2026-11-10")
    assert client.get(f"/api/meetings/{mid}/result").status_code == 404
    assert client.post("/api/meetings", data={"title": "x"}).status_code == 422
