"""T-03-1 (B): 검증 모듈 단위 TEST — Action, ActionCandidate, MeetingAnalysis, FollowupResult,
ReviewResult, ReviewQueueItem 각각의 정상 입력과 대표적인 계약 위반 (SPEC 5.2~5.6, 6.3, 6.4, 9장).

TEST 단계: 구현 전이므로 FAIL(RED)이 정상이다 (meeting_action_agent.contracts 미구현).

validate()는 스키마 검증만 한다. 도메인/안전 가드의 예외(CompletionGuardError 등)는
적용 지점(receive_agent_output, ActionStore)의 책임이며 test_schema_application_points.py에서 다룬다.
"""
import copy
import json
from pathlib import Path

import pytest

from meeting_action_agent.contracts import SchemaValidationError, validate
from meeting_action_agent.state import CompletionGuardError, TransitionError

VALID = Path(__file__).resolve().parents[2] / "specs" / "001-meeting-action-agent" / "contracts" / "examples" / "valid"


def _ex(name):
    return json.loads((VALID / name).read_text(encoding="utf-8"))


def _mutated(name, mutate):
    data = copy.deepcopy(_ex(name))
    mutate(data)
    return data


def test_schema_validation_error_is_distinct_from_domain_guard_errors():
    assert issubclass(SchemaValidationError, Exception)
    assert not issubclass(SchemaValidationError, (CompletionGuardError, TransitionError))


# ---------- 정상 입력 ----------

@pytest.mark.parametrize(
    "schema,example",
    [
        ("action", "action.json"),
        ("action", "action-completed.json"),
        ("action", "action-withdrawn.json"),
        ("action", "action-reassessment.json"),
        ("action-candidate", "action-candidate.json"),
        ("action-candidate", "action-candidate-conditional.json"),
        ("action-candidate", "action-candidate-unclear.json"),
        ("meeting-analysis", "meeting-analysis.json"),
        ("followup-result", "followup-result.json"),
        ("followup-result", "followup-result-no-mention.json"),
        ("review-result", "review-result-reject.json"),
        ("review-queue-item", "review-queue-item.json"),
    ],
)
def test_valid_inputs_pass(schema, example):
    validate(schema, _ex(example))


# ---------- 계약 위반: SchemaValidationError ----------

VIOLATIONS = [
    # Action (SPEC 5.2, 5.4, US-05)
    ("action", "action.json", lambda d: d.update(status="완료"), "승인 없는 완료"),
    ("action", "action.json", lambda d: d.update(memo="임의 필드"), "정의 외 필드"),
    ("action", "action.json", lambda d: d.update(due_date="미정"), "미정인데 환산 날짜"),
    ("action", "action.json", lambda d: d.update(timestamp="12:61:00"), "타임스탬프 형식"),
    ("action", "action.json", lambda d: d.pop("evidence"), "필수 필드 누락"),
    ("action", "action.json", lambda d: d.update(status="취소"), "정의 외 status"),
    ("action", "action.json", lambda d: d["history"][0].update(actor="사람"), "사람의 최초 생성"),
    # ActionCandidate (SPEC 9장, 5.5)
    ("action-candidate", "action-candidate.json", lambda d: d.update(status="완료"), "Agent의 완료"),
    ("action-candidate", "action-candidate.json", lambda d: d.update(action_id="A-001"), "사람/코드 전용 action_id"),
    ("action-candidate", "action-candidate.json", lambda d: d.update(approved_by_human=None), "사람 전용 approved_by_human"),
    ("action-candidate", "action-candidate.json", lambda d: d.update(history=[]), "코드 전용 history"),
    ("action-candidate", "action-candidate.json", lambda d: d.update(flags=["조건부"]), "조건부인데 신규·조건 없음"),
    ("action-candidate", "action-candidate.json", lambda d: d.update(status="취소"), "정의 외 status"),
    # MeetingAnalysis (US-06)
    ("meeting-analysis", "meeting-analysis.json",
     lambda d: d["key_discussions"][0].update(evidence_timestamps=[]), "요약 항목에 근거 타임스탬프 없음"),
    ("meeting-analysis", "meeting-analysis.json",
     lambda d: d["action_candidates"][0]["candidate"].update(status="완료"), "후보의 완료"),
    ("meeting-analysis", "meeting-analysis.json", lambda d: d.pop("decisions"), "필수 목록 누락"),
    # FollowupResult (SPEC 5.6, US-04)
    ("followup-result", "followup-result.json", lambda d: d.update(proposed_status="완료"), "Agent의 완료"),
    ("followup-result", "followup-result.json", lambda d: d.update(evidence=[]), "근거 없는 완료 후보"),
    ("followup-result", "followup-result.json", lambda d: d.update(confidence="하"), "확신도 하인데 완료 후보"),
    # ReviewResult (SPEC 6.3, 6.4)
    ("review-result", "review-result-reject.json", lambda d: d.update(reasons=[]), "사유 없는 REJECT"),
    ("review-result", "review-result-reject.json", lambda d: d.update(attempt=3), "재시도 상한 초과"),
    ("review-result", "review-result-reject.json", lambda d: d.update(verdict="MAYBE"), "정의 외 verdict"),
    # ReviewQueueItem (SPEC 6.4)
    ("review-queue-item", "review-queue-item.json",
     lambda d: d["candidate"].update(status="신규"), "큐 항목이 확인 필요가 아님"),
    ("review-queue-item", "review-queue-item.json", lambda d: d.pop("queued_at"), "필수 필드 누락"),
    ("review-queue-item", "review-queue-item.json", lambda d: d.update(resolution={"decision": "폐기"}), "불완전한 resolution"),
]


@pytest.mark.parametrize(
    "schema,example,mutate",
    [v[:3] for v in VIOLATIONS],
    ids=[f"{v[0]}:{v[3]}" for v in VIOLATIONS],
)
def test_contract_violations_raise_schema_validation_error(schema, example, mutate):
    with pytest.raises(SchemaValidationError):
        validate(schema, _mutated(example, mutate))


def test_schema_validation_error_message_names_the_violation():
    """어느 필드가 계약을 어겼는지 메시지로 알 수 있어야 한다."""
    data = _mutated("action.json", lambda d: d.update(timestamp="12:61:00"))
    with pytest.raises(SchemaValidationError) as info:
        validate("action", data)
    assert "timestamp" in str(info.value)
