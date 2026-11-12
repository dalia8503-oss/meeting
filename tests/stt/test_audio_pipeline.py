"""Audio → Transcript → 기존 pipeline 연결 (scripts/run_audio_pipeline.py). 외부 API 없음: 고정 STT 응답 + stub runner."""
import importlib.util
import json
from pathlib import Path

import pytest

from meeting_action_agent.stt.gemini_stt import GeminiSttProvider

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "stt"
_spec = importlib.util.spec_from_file_location("run_audio_pipeline", ROOT / "scripts" / "run_audio_pipeline.py")
rap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rap)

MID = "20261110-01"
SAMPLE = (FIX / "gemini_response_sample.json").read_text(encoding="utf-8")
SMAP = {"speaker_1": {"name": "팀장"}, "speaker_2": {"name": "김대리"}}
SENT = "김대리, 금요일까지 도면 수정해서 공유해주세요."


def analyst(prompt):
    assert SENT in prompt
    return json.dumps({
        "meeting_id": MID,
        "key_discussions": [{"text": "도면 작업 진행 확인", "evidence_timestamps": ["00:00:08"]}],
        "decisions": [], "open_issues": [], "needs_confirmation": [],
        "action_candidates": [{"instruction_type": "지시·요청", "candidate": {
            "meeting_id": MID, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정해서 공유",
            "due_date": "금요일까지", "due_date_normalized": None, "evidence": SENT,
            "timestamp": "00:00:12", "status": "신규", "flags": []}}],
    }, ensure_ascii=False)


def reviewer(prompt):
    return {"target": {"type": "action", "ref": "00:00:12"}, "verdict": "PASS", "reasons": [], "attempt": 0}


@pytest.fixture
def audio(tmp_path):
    return FIX / "tts_meeting_001.wav"


def run(tmp_path, audio, smap=SMAP, provider=None):
    return rap.run_audio_pipeline(
        audio, meeting_id=MID, meeting_date="2026-11-10", root=tmp_path / "data", store_path=tmp_path / "actions.json",
        provider=provider or GeminiSttProvider(call=lambda *a: SAMPLE),
        analyst_runner=analyst, reviewer_runner=reviewer, speaker_map=smap,
    )


def test_audio_to_action_list(tmp_path, audio):
    r = run(tmp_path, audio)
    assert r["utterances"] == 8 and r["rejected"] == []
    assert len(r["actions"]) == 1  # 제안·질문은 Action이 아니다
    a = r["actions"][0]
    assert (a["action_id"], a["assignee"], a["assigner"], a["due_date"]) == ("A-001", "김대리", "팀장", "금요일까지")
    assert a["due_date_normalized"] == "2026-11-13" and a["status"] == "신규"


def test_files_created_in_expected_places(tmp_path, audio):
    run(tmp_path, audio)
    d = tmp_path / "data"
    assert (d / "audio" / f"{MID}.wav").read_bytes() == audio.read_bytes()
    assert (d / "meetings" / f"{MID}.meta.json").is_file() and (d / "meetings" / f"{MID}.audio.json").is_file()
    t = json.loads((d / "transcripts" / "original" / f"{MID}.json").read_text(encoding="utf-8"))
    assert t[2]["speaker"] == "팀장" and t[2]["text"] == SENT and t[2]["timestamp"] == "00:00:12"


def test_without_speaker_map_labels_are_kept(tmp_path, audio):
    run(tmp_path, audio, smap=None)
    t = json.loads((tmp_path / "data" / "transcripts" / "original" / f"{MID}.json").read_text(encoding="utf-8"))
    assert {u["speaker"] for u in t} == {"speaker_1", "speaker_2", "speaker_3", "speaker_4"}


def test_stt_provider_is_not_called_again_when_original_transcript_exists(tmp_path, audio):
    run(tmp_path, audio)
    calls = []

    def spy(*a):
        calls.append(1)
        return SAMPLE

    original = (tmp_path / "data" / "transcripts" / "original" / f"{MID}.json").read_bytes()
    run(tmp_path, audio, provider=GeminiSttProvider(call=spy))  # 재실행: 원본 재사용
    assert not calls
    assert (tmp_path / "data" / "transcripts" / "original" / f"{MID}.json").read_bytes() == original


def test_tampered_audio_stops_rerun(tmp_path, audio):
    run(tmp_path, audio)
    with open(tmp_path / "data" / "audio" / f"{MID}.wav", "ab") as f:
        f.write(b"\x00")
    with pytest.raises(RuntimeError, match="해시"):
        run(tmp_path, audio)


def test_stt_failure_leaves_no_transcript_and_can_retry(tmp_path, audio):
    def boom(*a):
        raise RuntimeError("timeout")

    with pytest.raises(Exception):
        run(tmp_path, audio, provider=GeminiSttProvider(call=boom))
    assert not (tmp_path / "data" / "transcripts").exists()
    assert len(run(tmp_path, audio)["actions"]) == 1  # 같은 meeting_id로 재시도 가능 (원본 녹음 재사용)
