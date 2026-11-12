"""T-00-13: TTS 가상 회의 음성 + 정답 대본의 일관성 (외부 API 없음)."""
import json
import wave
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "stt"
ANS = json.loads((FIX / "tts_meeting_001.answer.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((FIX / "gemini_response_sample.json").read_text(encoding="utf-8"))["segments"]


def test_audio_length_30_to_60_seconds():
    with wave.open(str(FIX / ANS["audio"])) as w:
        sec = w.getnframes() / w.getframerate()
    assert 30 <= sec <= 60
    assert abs(sec - ANS["duration_sec"]) < 0.5


def test_required_utterances_present():
    texts = [u["text"] for u in ANS["utterances"]]
    for t in ("김대리, 금요일까지 도면 수정해서 공유해주세요.", "네, 알겠습니다.",
              "다음 회의에서는 품질 문제도 다뤄보면 어떨까요?", "도면 검토는 끝났나요?"):
        assert t in texts
    assert 6 <= len(texts) <= 8 + 2


def test_exactly_one_expected_action():
    assert len(ANS["expected_actions"]) == 1
    a = ANS["expected_actions"][0]
    assert (a["assignee"], a["due_expression"]) == ("김대리", "금요일")
    assert len(ANS["not_actions"]) == 2  # 제안·질문은 Action이 아니다


def test_answer_timestamps_are_chronological_and_in_range():
    secs = [u["start_sec"] for u in ANS["utterances"]]
    assert secs == sorted(secs) and secs[0] == 0 and secs[-1] < ANS["duration_sec"]


def test_speaker_labels_and_map_are_consistent():
    labels = {u["speaker"] for u in ANS["utterances"]}
    assert labels == set(ANS["speaker_map"])
    assert all(l.startswith("speaker_") for l in labels)


def test_no_real_company_or_personal_info():
    blob = json.dumps(ANS, ensure_ascii=False)
    for bad in ("@", "주식회사", "(주)", "010-"):
        assert bad not in blob


def test_sample_response_matches_answer_text():
    """고정 응답 fixture는 정답 대본과 같은 발언 순서·화자를 가진다 (손으로 작성, 실제 Gemini 출력 아님)."""
    assert [(s["speaker"], s["text"]) for s in SAMPLE] == [(u["speaker"], u["text"]) for u in ANS["utterances"]]
