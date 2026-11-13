"""Speaker Identification Agent 출력 검증 (순수 함수). 외부 호출 없음."""
import json

import pytest

from meeting_action_agent.speaker_identifier import (
    SpeakerSuggestionError, build_prompt, confidence_level, identify_speakers, validate_suggestions)

T = [
    {"speaker": "speaker_1", "timestamp": "00:00:01", "text": "정 과장님, 도면은 어떻게 됐나요?"},
    {"speaker": "speaker_2", "timestamp": "00:00:05", "text": "네, 공유했습니다."},
    {"speaker": "speaker_3", "timestamp": "00:00:09", "text": "알겠습니다."},
]


def _sug(out, label):
    return next(s for s in out if s["speaker_id"] == label)


def test_grounded_name_with_evidence_is_kept():
    raw = {"speaker_2": {"suggested_name": "정과장", "confidence": 0.9, "evidence": ["호명 직후 응답"]}}
    s = _sug(validate_suggestions(json.dumps(raw), T), "speaker_2")
    assert s["suggested_name"] == "정과장" and s["confidence"] == 0.9 and s["evidence"] == ["호명 직후 응답"]


def test_every_transcript_speaker_is_returned_missing_ones_are_null():
    out = validate_suggestions({}, T)
    assert [s["speaker_id"] for s in out] == ["speaker_1", "speaker_2", "speaker_3"]
    assert all(s["suggested_name"] is None and s["confidence"] == 0.0 and s["evidence"] == []
               and s["needs_confirmation"] is True for s in out)


@pytest.mark.parametrize("name", ["이사장", "박대리", "김부장"])  # 녹취에 없는 이름은 만들 수 없다
def test_name_not_in_transcript_is_nulled(name):
    raw = {"speaker_2": {"suggested_name": name, "confidence": 0.95, "evidence": ["추측"]}}
    s = _sug(validate_suggestions(raw, T), "speaker_2")
    assert s["suggested_name"] is None and s["confidence"] == 0.0 and s["needs_confirmation"] is True


def test_title_only_is_nulled():
    raw = {"speaker_2": {"suggested_name": "과장님", "confidence": 0.9, "evidence": ["호칭"]}}
    assert _sug(validate_suggestions(raw, T), "speaker_2")["suggested_name"] is None


def test_name_without_evidence_is_nulled():
    raw = {"speaker_2": {"suggested_name": "정과장", "confidence": 0.9, "evidence": []}}
    assert _sug(validate_suggestions(raw, T), "speaker_2")["suggested_name"] is None


def test_unknown_speaker_dropped_and_confidence_clamped():
    raw = {"speaker_9": {"suggested_name": "정과장", "confidence": 0.9, "evidence": ["x"]},
           "speaker_2": {"suggested_name": "정과장", "confidence": 7, "evidence": ["x"]}}
    out = validate_suggestions(raw, T)
    assert {s["speaker_id"] for s in out} == {"speaker_1", "speaker_2", "speaker_3"}
    assert _sug(out, "speaker_2")["confidence"] == 1.0


def test_same_name_for_two_speakers_is_conflict():
    raw = {"speaker_2": {"suggested_name": "정과장", "confidence": 0.9, "evidence": ["a"]},
           "speaker_3": {"suggested_name": "정 과장", "confidence": 0.9, "evidence": ["b"]}}
    out = validate_suggestions(raw, T)
    for label in ("speaker_2", "speaker_3"):
        s = _sug(out, label)
        assert s["needs_confirmation"] is True and confidence_level(s["confidence"]) == "low"


def test_non_json_output_raises():
    with pytest.raises(SpeakerSuggestionError):
        validate_suggestions("이건 JSON이 아니다", T)


@pytest.mark.parametrize("c,level", [(0.92, "high"), (0.85, "high"), (0.84, "medium"), (0.6, "medium"), (0.59, "low"), (0.0, "low")])
def test_confidence_levels(c, level):
    assert confidence_level(c) == level


def test_prompt_contains_transcript_and_rules_and_no_speaker_map():
    prompt = build_prompt(T, [{"speaker": "speaker_1", "samples": [{"timestamp": "00:00:01", "text": "x"}]}])
    assert "정 과장님" in prompt and "생성 금지" in prompt and "speaker_map" not in prompt.split("## INPUT")[1]


def test_identify_speakers_uses_runner_only():
    seen = []

    def runner(prompt):
        seen.append(prompt)
        return json.dumps({"speaker_2": {"suggested_name": "정과장", "confidence": 0.8, "evidence": ["e"]}})

    out = identify_speakers(T, [{"speaker": "speaker_2", "samples": []}], runner)
    assert len(seen) == 1 and _sug(out, "speaker_2")["suggested_name"] == "정과장"


# ---- 조직정보(부/과): 녹취에 이름과 함께 명시된 경우에만 추천
ORG_T = [
    {"speaker": "speaker_1", "timestamp": "00:00:01", "text": "1부 3과 이과장님, 이 건 확인해주세요."},
    {"speaker": "speaker_2", "timestamp": "00:00:05", "text": "네, 확인하겠습니다."},
]
PLAIN_T = [
    {"speaker": "speaker_1", "timestamp": "00:00:01", "text": "이과장님 확인해주세요."},
    {"speaker": "speaker_2", "timestamp": "00:00:05", "text": "네."},
]


def _raw(**kw):
    return {"speaker_2": {"suggested_name": "이과장", "confidence": 0.8, "evidence": ["호명 직후 응답"], **kw}}


def test_org_with_name_in_transcript_is_suggested():
    s = _sug(validate_suggestions(_raw(department="1부", section="3과"), ORG_T), "speaker_2")
    assert (s["department"], s["section"], s["suggested_name"]) == ("1부", "3과", "이과장")


def test_org_not_in_transcript_is_nulled_but_name_kept():
    s = _sug(validate_suggestions(_raw(department="1부", section="3과"), PLAIN_T), "speaker_2")
    assert (s["department"], s["section"], s["suggested_name"]) == (None, None, "이과장")


def test_wrong_org_is_nulled():
    s = _sug(validate_suggestions(_raw(department="2부", section="1과"), ORG_T), "speaker_2")
    assert s["department"] is None and s["section"] is None and s["suggested_name"] == "이과장"


def test_org_without_name_is_dropped():
    raw = {"speaker_2": {"suggested_name": None, "department": "1부", "section": "3과", "confidence": 0.8, "evidence": ["x"]}}
    s = _sug(validate_suggestions(raw, ORG_T), "speaker_2")
    assert s["department"] is None and s["section"] is None and s["suggested_name"] is None
