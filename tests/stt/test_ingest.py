"""T-01-1/T-01-2/T-01-4: 녹음 입력·사양 검증·원본 보호·해시 (외부 API 없음)."""
import hashlib
import json
import struct
import wave
from pathlib import Path

import pytest

from meeting_action_agent.ingest import IngestError, ingest_audio, validate_audio, verify_audio_hash

MID = "20261110-01"
META = {"meeting_id": MID, "meeting_date": "2026-11-10"}
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "stt"


def write_wav(path, seconds=1, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def fake_mp3(path):
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 50)
    return path


def fake_m4a(path):
    path.write_bytes(struct.pack(">I", 24) + b"ftypM4A " + b"\x00" * 100)
    return path


@pytest.fixture
def root(tmp_path):
    return tmp_path / "root"


# --- 형식 (T-01-2) -----------------------------------------------------------
@pytest.mark.parametrize("maker,ext", [(write_wav, ".wav"), (fake_mp3, ".mp3"), (fake_m4a, ".m4a")])
def test_supported_formats_pass(tmp_path, maker, ext):
    info = validate_audio(maker(tmp_path / f"a{ext}"))
    assert info["ext"] == ext


def test_uppercase_extension_ok(tmp_path):
    assert validate_audio(write_wav(tmp_path / "a.WAV"))["ext"] == ".wav"


@pytest.mark.parametrize("name", ["a.ogg", "a.flac", "a.txt", "a"])
def test_unsupported_format_rejected(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x" * 100)
    with pytest.raises(IngestError, match="형식"):
        validate_audio(p)


def test_missing_and_empty_file_rejected(tmp_path):
    with pytest.raises(IngestError):
        validate_audio(tmp_path / "none.wav")
    e = tmp_path / "e.mp3"
    e.write_bytes(b"")
    with pytest.raises(IngestError):
        validate_audio(e)


@pytest.mark.parametrize("ext", [".wav", ".mp3", ".m4a"])
def test_extension_content_mismatch_rejected(tmp_path, ext):
    p = tmp_path / f"fake{ext}"
    p.write_bytes(b"this is plain text, not audio at all")
    with pytest.raises(IngestError):
        validate_audio(p)


def test_wav_over_two_hours_rejected(tmp_path):
    p = write_wav(tmp_path / "long.wav", seconds=3)
    with pytest.raises(IngestError, match="2시간"):
        validate_audio(p, max_duration_sec=2)


def test_wav_at_limit_ok(tmp_path):
    p = write_wav(tmp_path / "ok.wav", seconds=2)
    assert validate_audio(p, max_duration_sec=2)["duration_sec"] == 2


def test_mp3_duration_is_unknown_not_faked(tmp_path):
    assert validate_audio(fake_mp3(tmp_path / "a.mp3"))["duration_sec"] is None


def test_real_tts_fixture_is_valid():
    info = validate_audio(FIX / "tts_meeting_001.wav")
    assert 30 <= info["duration_sec"] <= 60


# --- 업로드·메타 (T-01-1) ----------------------------------------------------
def test_ingest_creates_audio_and_meta(tmp_path, root):
    src = write_wav(tmp_path / "in.wav")
    r = ingest_audio(src, {**META, "title": "t", "participants": ["김대리"]}, root)
    assert r.audio_path == root / "audio" / f"{MID}.wav" and r.audio_path.read_bytes() == src.read_bytes()
    assert json.loads(r.meta_path.read_text(encoding="utf-8"))["participants"] == ["김대리"]
    assert r.meta_path == root / "meetings" / f"{MID}.meta.json"


def test_ingest_keeps_source_file(tmp_path, root):
    src = write_wav(tmp_path / "in.wav")
    before = src.read_bytes()
    ingest_audio(src, META, root)
    assert src.read_bytes() == before


@pytest.mark.parametrize("meta", [
    {"meeting_id": MID},                                    # meeting_date 필수
    {"meeting_date": "2026-11-10"},                         # meeting_id 필수
    {**META, "meeting_date": "2026-13-40"},                 # 존재하지 않는 날짜
    {**META, "meeting_date": "11/10/2026"},
    {**META, "unknown_field": 1},
    {**META, "speaker_map": {"speaker_1": {"position": "팀장"}}},  # name 없음
])
def test_invalid_metadata_rejected_nothing_written(tmp_path, root, meta):
    with pytest.raises(Exception):
        ingest_audio(write_wav(tmp_path / "in.wav"), meta, root)
    assert not root.exists() or not list(root.rglob("*.*"))


@pytest.mark.parametrize("mid", ["../x", "a/b", "a\\b", ".."])
def test_unsafe_meeting_id_rejected(tmp_path, root, mid):
    with pytest.raises(ValueError):
        ingest_audio(write_wav(tmp_path / "in.wav"), {**META, "meeting_id": mid}, root)


def test_invalid_audio_writes_nothing(tmp_path, root):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    with pytest.raises(IngestError):
        ingest_audio(bad, META, root)
    assert not root.exists() or not list(root.rglob("*.*"))


# --- 원본 보호·해시 (T-01-1, T-01-3, T-01-4) -----------------------------------
def test_ingest_never_overwrites_existing_audio(tmp_path, root):
    r = ingest_audio(write_wav(tmp_path / "a.wav", seconds=1), META, root)
    before = r.audio_path.read_bytes()
    with pytest.raises(FileExistsError):
        ingest_audio(write_wav(tmp_path / "b.wav", seconds=2), META, root)
    assert r.audio_path.read_bytes() == before


def test_hash_recorded_matches_sha256(tmp_path, root):
    r = ingest_audio(write_wav(tmp_path / "a.wav"), META, root)
    rec = json.loads(r.hash_path.read_text(encoding="utf-8"))
    assert rec["sha256"] == hashlib.sha256(r.audio_path.read_bytes()).hexdigest() == r.sha256
    assert rec["file"] == f"audio/{MID}.wav" and r.hash_path == root / "meetings" / f"{MID}.audio.json"


def test_hash_unchanged_after_upload(tmp_path, root):
    r = ingest_audio(write_wav(tmp_path / "a.wav"), META, root)
    assert verify_audio_hash(root, MID) is True


def test_tampering_detected(tmp_path, root):
    r = ingest_audio(write_wav(tmp_path / "a.wav"), META, root)
    with open(r.audio_path, "ab") as f:
        f.write(b"\x01")
    assert verify_audio_hash(root, MID) is False
