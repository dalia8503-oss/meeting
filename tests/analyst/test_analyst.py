"""T-04-2/3/4: Analyst 실행 계층 — 핵심 케이스만. runner는 고정 응답 stub (실제 LLM 호출 아님)."""
import copy
import json

import pytest

from meeting_action_agent.analyst import (
    AnalysisValidationError, analyze_transcript, save_analysis, validate_analysis,
)
from meeting_action_agent.contracts import SchemaValidationError

MID = "20261110-01"
SENT = "김대리, 금요일까지 도면 수정해서 공유해주세요."
TRANSCRIPT = [
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:01:00", "text": "도면 검토 결과를 공유합니다."},
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:02:10", "text": SENT},
]


def _analysis():
    return {
        "meeting_id": MID,
        "key_discussions": [{"text": "도면 검토 결과 공유", "evidence_timestamps": ["00:01:00"]}],
        "decisions": [], "open_issues": [], "needs_confirmation": [],
        "action_candidates": [{
            "instruction_type": "지시·요청",
            "candidate": {
                "meeting_id": MID, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정 후 공유",
                "due_date": "금요일까지", "due_date_normalized": None, "evidence": SENT,
                "timestamp": "00:02:10", "status": "신규", "flags": [],
            },
        }],
    }


def test_smoke_transcript_to_analysis_json(tmp_path):
    prompts = []

    def runner(p):
        prompts.append(p)
        return json.dumps(_analysis(), ensure_ascii=False)

    analysis = analyze_transcript(TRANSCRIPT, runner)
    assert SENT in prompts[0] and "Meeting Analyst" in prompts[0]
    path = save_analysis(analysis, TRANSCRIPT, tmp_path / "meetings")
    assert path.name == f"{MID}.analysis.json"
    assert json.loads(path.read_text(encoding="utf-8")) == analysis


def test_empty_candidates_allowed():
    a = _analysis()
    a["action_candidates"] = []
    validate_analysis(a, TRANSCRIPT)


def test_schema_violation_rejected():
    a = _analysis()
    a["decisions"] = [{"text": "x", "evidence_timestamps": []}]
    with pytest.raises(SchemaValidationError):
        validate_analysis(a, TRANSCRIPT)


def test_evidence_timestamp_not_in_transcript():
    a = _analysis()
    a["decisions"] = [{"text": "x", "evidence_timestamps": ["00:09:59"]}]
    with pytest.raises(AnalysisValidationError, match="없음"):
        validate_analysis(a, TRANSCRIPT)


def test_candidate_timestamp_not_in_transcript():
    a = _analysis()
    a["action_candidates"][0]["candidate"]["timestamp"] = "00:05:00"
    with pytest.raises(AnalysisValidationError, match="없음"):
        validate_analysis(a, TRANSCRIPT)


def test_meeting_id_mismatch():
    a = _analysis()
    a["meeting_id"] = "other"
    with pytest.raises(AnalysisValidationError, match="meeting_id"):
        validate_analysis(a, TRANSCRIPT)
    b = _analysis()
    b["action_candidates"][0]["candidate"]["meeting_id"] = "other"
    with pytest.raises(AnalysisValidationError, match="meeting_id"):
        validate_analysis(b, TRANSCRIPT)


def test_duplicate_timestamp_fails_unless_safe_ambiguous():
    t = copy.deepcopy(TRANSCRIPT)
    t.append({**t[1], "speaker": "지영", "text": "네."})
    with pytest.raises(AnalysisValidationError, match="특정할 수 없음"):
        validate_analysis(_analysis(), t)
    s = _analysis()
    s["action_candidates"][0]["instruction_type"] = "지시여부애매"
    s["action_candidates"][0]["candidate"].update(
        status="확인 필요", flags=["지시여부애매"], flag_notes={"지시여부애매": {"note": "동일 timestamp 발언 2건"}})
    validate_analysis(s, t)


def test_invalid_result_not_saved(tmp_path):
    a = _analysis()
    a["key_discussions"][0]["evidence_timestamps"] = ["00:07:00"]
    with pytest.raises(AnalysisValidationError):
        save_analysis(a, TRANSCRIPT, tmp_path)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(AnalysisValidationError):
        analyze_transcript(TRANSCRIPT, lambda p: "not json")
