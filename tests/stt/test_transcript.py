"""T-02-2/T-02-3/T-02-5/T-02-6: STT 세그먼트 → Transcript 변환·검증·원본 보호 (외부 API 없음)."""
import json
from pathlib import Path

import pytest

from meeting_action_agent.contracts import validate
from meeting_action_agent.stt.transcript import (
    TranscriptError,
    build_transcript,
    normalize_speaker,
    normalize_timestamp,
    save_original_transcript,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "stt"
MID = "20261110-01"


def seg(speaker="speaker_1", ts="00:05", text="안녕하세요."):
    return {"speaker": speaker, "timestamp": ts, "text": text}


@pytest.fixture
def sample_segments():
    return json.loads((FIX / "gemini_response_sample.json").read_text(encoding="utf-8"))["segments"]


# --- timestamp -------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("00:05", "00:00:05"), ("1:07", "00:01:07"), ("75:10", "01:15:10"),
    ("00:01:30", "00:01:30"), ("1:02:03", "01:02:03"),
])
def test_normalize_timestamp(raw, want):
    assert normalize_timestamp(raw) == want


@pytest.mark.parametrize("raw", ["", "abc", "12", "00:60", "00:00:61", "-1:00", "00:00:00:01", "1.5"])
def test_normalize_timestamp_rejects_bad_format(raw):
    with pytest.raises(TranscriptError):
        normalize_timestamp(raw)


# --- speaker ---------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("speaker_1", "speaker_1"), ("Speaker 2", "speaker_2"), ("SPEAKER_03", "speaker_3"), (" speaker-4 ", "speaker_4"),
])
def test_normalize_speaker_label(raw, want):
    assert normalize_speaker(raw) == want


@pytest.mark.parametrize("raw", ["김대리", "팀장", "화자", "", "Kim", "speaker"])
def test_names_guessed_by_stt_are_rejected(raw):
    """STT가 실명·직책을 추측해 내면 받지 않는다 (이름은 사람이 준 speaker_map으로만)."""
    with pytest.raises(TranscriptError):
        normalize_speaker(raw)


# --- build_transcript ------------------------------------------------------
def test_build_keeps_speaker_labels_without_speaker_map(sample_segments):
    t = build_transcript(sample_segments, MID)
    assert {u["speaker"] for u in t} == {"speaker_1", "speaker_2", "speaker_3", "speaker_4"}
    assert t[0] == {"meeting_id": MID, "speaker": "speaker_1", "timestamp": "00:00:00",
                    "text": "자, 그럼 회의를 시작하겠습니다. 오늘은 지난주 진행 상황부터 확인하겠습니다."}


def test_output_satisfies_transcript_schema(sample_segments):
    validate("transcript", build_transcript(sample_segments, MID))


def test_speaker_map_applied_only_when_given(sample_segments):
    smap = {"speaker_1": {"name": "팀장"}, "speaker_2": {"name": "김대리"}}
    t = build_transcript(sample_segments, MID, speaker_map=smap)
    by_label = {s["speaker"]: u["speaker"] for s, u in zip(sample_segments, t)}
    assert by_label == {"speaker_1": "팀장", "speaker_2": "김대리", "speaker_3": "speaker_3", "speaker_4": "speaker_4"}


def test_unmapped_label_stays_as_is():
    t = build_transcript([seg("speaker_9")], MID, speaker_map={"speaker_1": {"name": "팀장"}})
    assert t[0]["speaker"] == "speaker_9"


def test_text_is_not_modified():
    raw = "  음… 그 도면은요,  금요일까지   입니다.  "
    assert build_transcript([seg(text=raw)], MID)[0]["text"] == raw.strip()


def test_low_confidence_is_never_invented(sample_segments):
    """Gemini Audio는 신뢰도 지표가 없다 → 가짜 confidence 금지: 필드 자체를 만들지 않는다."""
    assert all("low_confidence" not in u for u in build_transcript(sample_segments, MID))


def test_segment_extra_confidence_is_not_converted_to_low_confidence():
    s = {**seg(), "confidence": 0.1, "low_confidence": True}
    assert "low_confidence" not in build_transcript([s], MID)[0]


def test_timestamps_must_be_chronological():
    with pytest.raises(TranscriptError, match="시간순"):
        build_transcript([seg(ts="00:30"), seg(ts="00:10")], MID)


def test_equal_timestamps_allowed():
    assert len(build_transcript([seg(ts="00:10"), seg(ts="00:10")], MID)) == 2


@pytest.mark.parametrize("bad", [
    [], [{"speaker": "speaker_1", "timestamp": "00:01"}], [seg(text="")], [seg(text="   ")],
    [{"speaker": "speaker_1", "text": "x"}], ["not a dict"],
])
def test_malformed_segments_rejected(bad):
    with pytest.raises(TranscriptError):
        build_transcript(bad, MID)


@pytest.mark.parametrize("mid", ["", "../evil", "a/b", "a\\b", "..", "x:y"])
def test_unsafe_meeting_id_rejected(mid):
    with pytest.raises(ValueError):
        build_transcript([seg()], mid)


# --- 원본 보호 (T-02-5, T-02-6) ---------------------------------------------
def test_save_creates_original_and_returns_path(tmp_path, sample_segments):
    t = build_transcript(sample_segments, MID)
    p = save_original_transcript(t, tmp_path)
    assert p == tmp_path / "transcripts" / "original" / f"{MID}.json"
    assert json.loads(p.read_text(encoding="utf-8")) == t


def test_save_never_overwrites_original(tmp_path, sample_segments):
    t = build_transcript(sample_segments, MID)
    p = save_original_transcript(t, tmp_path)
    before = p.read_bytes()
    with pytest.raises(FileExistsError):
        save_original_transcript(build_transcript([seg(text="다른 내용")], MID), tmp_path)
    assert p.read_bytes() == before


def test_save_rejects_invalid_transcript_without_writing(tmp_path):
    bad = [{"meeting_id": MID, "speaker": "speaker_1", "timestamp": "bad", "text": "x"}]
    with pytest.raises(Exception):
        save_original_transcript(bad, tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_save_rejects_mixed_meeting_ids(tmp_path):
    t = build_transcript([seg(), seg(ts="00:09")], MID)
    t[1] = {**t[1], "meeting_id": "other"}
    with pytest.raises(TranscriptError):
        save_original_transcript(t, tmp_path)
