"""DELETE /api/meetings/{id} — 사용자가 명시한 회의 삭제. 외부 API(Gemini/TTS) 호출 없음, temp runtime만 사용."""
import hashlib
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


def upload(client, date, payload=None):
    r = client.post("/api/meetings", data={"title": "주간", "meeting_date": date},
                    files={"audio_file": ("m.wav", payload or WAV.read_bytes(), "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()["meeting_id"]


def ids(client):
    return [m["meeting_id"] for m in client.get("/api/meetings").json()["meetings"]]


def snapshot(base, exclude=()):
    """meeting별 파일이 아닌, base 아래 모든 파일의 {상대경로: sha256}."""
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob("*") if p.is_file() and not any(e in p.name for e in exclude)}


def files_of(base, mid):
    return [p for p in base.rglob("*") if p.is_file() and p.name.startswith(mid)]


def test_delete_uploaded_only_meeting(client, base):
    mid = upload(client, "2026-11-10")
    assert files_of(base, mid)
    r = client.delete(f"/api/meetings/{mid}")
    assert r.status_code == 200 and r.json() == {"deleted": True, "meeting_id": mid}
    assert files_of(base, mid) == [] and ids(client) == []


def test_delete_analyzed_meeting_removes_artifacts_and_origin_actions(client, base):
    mid = upload(client, "2026-11-10")
    assert client.post(f"/api/meetings/{mid}/analyze").status_code == 200
    assert json.loads((base / "actions" / "actions.json").read_text(encoding="utf-8"))
    r = client.delete(f"/api/meetings/{mid}")
    assert r.status_code == 200
    assert files_of(base, mid) == []
    assert json.loads((base / "actions" / "actions.json").read_text(encoding="utf-8")) == []
    assert ids(client) == []
    assert client.get(f"/api/meetings/{mid}/result").status_code == 404


def test_other_meeting_files_are_byte_identical(client, base):
    keep = upload(client, "2026-11-17", payload=WAV.read_bytes() + b"\x00")
    client.post(f"/api/meetings/{keep}/analyze")
    victim = upload(client, "2026-11-10")
    before = {k: v for k, v in snapshot(base).items() if not k.startswith("actions")}
    only_keep = {k: v for k, v in before.items() if victim not in k}
    assert client.delete(f"/api/meetings/{victim}").status_code == 200
    after = {k: v for k, v in snapshot(base).items() if not k.startswith("actions")}
    assert after == only_keep
    assert ids(client) == [keep]


def test_unknown_meeting_404(client):
    r = client.delete("/api/meetings/20260101-deadbeef")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "meeting_not_found"


@pytest.mark.parametrize("bad", ["..%2F..%2Fetc", "%2E%2E", "a%2F..%2Fb", "..", "C%3A%5Cwindows", "%2Fabs"])
def test_path_traversal_rejected(client, base, bad):
    keep = upload(client, "2026-11-10")
    before = snapshot(base)
    r = client.delete(f"/api/meetings/{bad}")
    assert r.status_code in (404, 405)
    assert snapshot(base) == before and ids(client) == [keep]


def test_meeting_used_as_previous_is_409_and_nothing_deleted(client, base):
    first = upload(client, "2026-11-10")
    client.post(f"/api/meetings/{first}/analyze")
    second = upload(client, "2026-11-17", payload=WAV.read_bytes() + b"\x00")
    assert client.post(f"/api/meetings/{second}/analyze", json={"previous_meeting_ids": [first]}).status_code == 200
    before = snapshot(base)
    r = client.delete(f"/api/meetings/{first}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "meeting_referenced"
    assert snapshot(base) == before
    # 참조하는 쪽(2차 회의)은 삭제 가능: 1차 회의를 근거로 한 Action이 없다면 지워지고 1차 회의는 유지된다.
    assert first in ids(client)


def test_action_tracked_in_other_meeting_is_409(client, base):
    first = upload(client, "2026-11-10")
    client.post(f"/api/meetings/{first}/analyze")
    second = upload(client, "2026-11-17", payload=WAV.read_bytes() + b"\x00")
    actions_path = base / "actions" / "actions.json"
    actions = json.loads(actions_path.read_text(encoding="utf-8"))
    actions[0]["extra_evidence"] = [{"meeting_id": second, "timestamp": "00:00:01", "speaker": "speaker_1", "text": "재언급"}]
    actions_path.write_text(json.dumps(actions, ensure_ascii=False), encoding="utf-8")
    assert client.delete(f"/api/meetings/{first}").status_code == 409
    assert first in ids(client)


def test_followup_file_referencing_action_is_409(client, base):
    first = upload(client, "2026-11-10")
    client.post(f"/api/meetings/{first}/analyze")
    second = upload(client, "2026-11-17", payload=WAV.read_bytes() + b"\x00")
    (base / "meetings" / f"{second}.followups.json").write_text(
        json.dumps([{"meeting_id": second, "action_id": "A-001"}]), encoding="utf-8")
    assert client.delete(f"/api/meetings/{first}").status_code == 409


def test_briefing_artifacts_of_this_meeting_only(client, base):
    mid = upload(client, "2026-11-10")
    other = upload(client, "2026-11-12", payload=WAV.read_bytes() + b"\x00")
    b = base / "briefings"
    b.mkdir()
    for owner, date in ((mid, "2026-11-17"), (other, "2026-11-17")):
        (b / f"{owner}_{date}.json").write_text(
            json.dumps({"briefing_id": f"{owner}_{date}", "previous_meeting_id": owner, "briefing_text": "x"}), encoding="utf-8")
        (b / f"{owner}_{date}.wav").write_bytes(b"RIFF")
    assert client.delete(f"/api/meetings/{mid}").status_code == 200
    assert not (b / f"{mid}_2026-11-17.json").exists() and not (b / f"{mid}_2026-11-17.wav").exists()
    assert (b / f"{other}_2026-11-17.json").exists() and (b / f"{other}_2026-11-17.wav").exists()


def test_unreadable_briefing_record_blocks_delete(client, base):
    mid = upload(client, "2026-11-10")
    (base / "briefings").mkdir()
    (base / "briefings" / f"{mid}_2026-11-17.json").write_text("{broken", encoding="utf-8")
    r = client.delete(f"/api/meetings/{mid}")
    assert r.status_code == 409 and mid in ids(client)


def test_delete_twice_second_is_404(client):
    mid = upload(client, "2026-11-10")
    assert client.delete(f"/api/meetings/{mid}").status_code == 200
    assert client.delete(f"/api/meetings/{mid}").status_code == 404
