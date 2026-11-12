"""POST /api/meetings — API 경계만 검증한다 (확장자·signature 세부 규칙은 tests/stt/test_ingest.py 소관)."""
import json
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from meeting_action_agent.api.app import create_app


def _wav_bytes(tmp_path, seconds=1, rate=8000, name="a.wav"):
    p = tmp_path / name
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return p.read_bytes()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    base = tmp_path / "runtime"
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(base))
    monkeypatch.delenv("MAA_MODE", raising=False)
    return base / "demo"  # 기본 mode=demo


@pytest.fixture
def client(runtime):
    return TestClient(create_app())


@pytest.fixture
def wav(tmp_path):
    return _wav_bytes(tmp_path)


def _post(client, wav, *, title="주간 회의", date="2026-11-12", filename="meeting.wav", extra=None, data=True):
    form = {"title": title, "meeting_date": date, **(extra or {})}
    return client.post("/api/meetings", data=form, files={"audio_file": (filename, wav, "audio/wav")})


def test_upload_ok_returns_meeting_id(client, wav):
    r = _post(client, wav)
    assert r.status_code == 201
    body = r.json()
    assert body["meeting_id"]
    assert body["title"] == "주간 회의"
    assert body["meeting_date"] == "2026-11-12"
    assert body["audio_filename"] == "meeting.wav"
    assert body["status"] == "uploaded"
    assert not {"analysis", "summary", "actions"} & body.keys()


def test_upload_persists_audio_metadata_and_hash(client, runtime, wav):
    mid = _post(client, wav).json()["meeting_id"]
    assert (runtime / "audio" / f"{mid}.wav").read_bytes() == wav
    meta = json.loads((runtime / "meetings" / f"{mid}.meta.json").read_text(encoding="utf-8"))
    assert meta["meeting_id"] == mid and meta["meeting_date"] == "2026-11-12" and meta["title"] == "주간 회의"
    h = json.loads((runtime / "meetings" / f"{mid}.audio.json").read_text(encoding="utf-8"))
    import hashlib
    assert h["sha256"] == hashlib.sha256(wav).hexdigest()


def test_bad_extension_rejected(client, runtime, wav):
    r = _post(client, wav, filename="meeting.txt")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_audio"
    assert not (runtime / "audio").exists()


def test_bad_signature_rejected(client, runtime):
    r = _post(client, b"this is not a wav file at all", filename="fake.wav")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_audio"
    assert not (runtime / "meetings").exists()


@pytest.mark.parametrize("kw,code", [
    ({"title": "  "}, "missing_title"),
    ({"date": ""}, "missing_meeting_date"),
    ({"date": "2026-13-45"}, "invalid_meeting_date"),
])
def test_form_field_errors(client, wav, kw, code):
    r = _post(client, wav, **kw)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == code


def test_missing_file(client):
    r = client.post("/api/meetings", data={"title": "t", "meeting_date": "2026-11-12"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_file"


def test_duplicate_meeting_returns_409_and_keeps_original(client, runtime, wav):
    first = _post(client, wav).json()
    audio = runtime / "audio" / f"{first['meeting_id']}.wav"
    before = (audio.stat().st_mtime_ns, audio.read_bytes())
    r = _post(client, wav, title="바뀐 제목")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate_meeting"
    assert (audio.stat().st_mtime_ns, audio.read_bytes()) == before
    meta = json.loads((runtime / "meetings" / f"{first['meeting_id']}.meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "주간 회의"


def test_mode_cannot_be_changed_by_request(client, tmp_path, wav):
    r = _post(client, wav, extra={"mode": "live"})
    assert r.status_code == 201
    base = tmp_path / "runtime"
    assert (base / "demo" / "audio").exists()
    assert not (base / "live").exists()


def test_live_mode_uses_separate_root(tmp_path, monkeypatch, wav):
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setenv("MAA_MODE", "live")
    assert _post(TestClient(create_app()), wav).status_code == 201
    assert (tmp_path / "rt" / "live" / "audio").exists()
    assert not (tmp_path / "rt" / "demo").exists()


def test_error_does_not_leak_paths(client, runtime, wav):
    r = _post(client, b"garbage", filename="x.wav")
    assert str(runtime) not in r.text and "Traceback" not in r.text and "\\" not in r.json()["detail"]["message"]
