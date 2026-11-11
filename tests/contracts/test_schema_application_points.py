"""T-03-1 (C): 적용 지점 TEST — Agent 출력 수신 / 저장 / 로드 (SPEC 9장, US-05, R-07).

TEST 단계: 구현 전이므로 FAIL(RED)이 정상이다 (meeting_action_agent.contracts 미구현).

예외 정책: 도메인/안전 가드 → Schema Validation 순서로 평가한다. 기존 가드의 예외 의미는 바꾸지 않는다.
  - 승인 없는 '완료', Agent 출력의 '완료'         → CompletionGuardError
  - 허용되지 않은 상태 전이·최초 생성 규칙 위반   → TransitionError
  - 계약의 필드/형식 위반                          → SchemaValidationError

가정한 인터페이스
  meeting_action_agent.contracts.receive_agent_output(schema_name, data)
    # Agent(LLM) 출력 수신 지점. 허용 이름: action-candidate, meeting-analysis, followup-result, review-result.
    # 그 외 이름(예: 'action')은 ValueError. 위반 시 위 예외 정책을 따른다.
  ActionStore.create / change_status / load_all / get 은 기존 API 그대로이며 스키마 검증이 추가된다.
"""
import copy
import json
from pathlib import Path

import pytest

from meeting_action_agent.contracts import SchemaValidationError, receive_agent_output
from meeting_action_agent.state import Actor, CompletionGuardError, Status, TransitionError
from meeting_action_agent.store import ActionStore

AGENT, HUMAN = Actor.AGENT, Actor.HUMAN
VALID = Path(__file__).resolve().parents[2] / "specs" / "001-meeting-action-agent" / "contracts" / "examples" / "valid"


def _ex(name):
    return copy.deepcopy(json.loads((VALID / name).read_text(encoding="utf-8")))


# =============== Agent 출력 수신 ===============

@pytest.mark.parametrize(
    "schema,example",
    [
        ("action-candidate", "action-candidate.json"),
        ("meeting-analysis", "meeting-analysis.json"),
        ("followup-result", "followup-result.json"),
        ("review-result", "review-result-reject.json"),
    ],
)
def test_receive_accepts_valid_agent_output(schema, example):
    receive_agent_output(schema, _ex(example))


def test_receive_rejects_done_status_in_candidate_with_guard_error():
    data = _ex("action-candidate.json")
    data["status"] = "완료"
    with pytest.raises(CompletionGuardError):
        receive_agent_output("action-candidate", data)


def test_receive_done_with_human_fields_is_still_a_guard_error():
    """도메인 가드가 스키마보다 우선: 완료 + 승인값을 Agent가 채워도 CompletionGuardError."""
    data = _ex("action-candidate.json")
    data.update(status="완료", approved_by_human=_ex("action-completed.json")["approved_by_human"])
    with pytest.raises(CompletionGuardError):
        receive_agent_output("action-candidate", data)


def test_receive_rejects_done_in_followup_result_with_guard_error():
    data = _ex("followup-result.json")
    data["proposed_status"] = "완료"
    with pytest.raises(CompletionGuardError):
        receive_agent_output("followup-result", data)


def test_receive_rejects_done_candidate_nested_in_meeting_analysis_with_guard_error():
    data = _ex("meeting-analysis.json")
    data["action_candidates"][0]["candidate"]["status"] = "완료"
    with pytest.raises(CompletionGuardError):
        receive_agent_output("meeting-analysis", data)


@pytest.mark.parametrize("status", ["취소", "보류", ""], ids=repr)
def test_receive_rejects_status_outside_the_contract(status):
    data = _ex("action-candidate.json")
    data["status"] = status
    with pytest.raises(SchemaValidationError):
        receive_agent_output("action-candidate", data)


@pytest.mark.parametrize(
    "field,value",
    [("action_id", "A-001"), ("approved_by_human", None), ("history", []), ("field_source", {})],
    ids=lambda v: str(v),
)
def test_receive_rejects_human_or_code_only_fields(field, value):
    data = _ex("action-candidate.json")
    data[field] = value
    with pytest.raises(SchemaValidationError):
        receive_agent_output("action-candidate", data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(timestamp="12:00:60"),  # timestamp는 경과시간: HH는 제한 없음, MM/SS만 00~59
        lambda d: d.pop("evidence"),
        lambda d: d.update(memo="정의 외 필드"),
        lambda d: d.update(due_date="확인 필요"),  # 환산 날짜가 남아 있음
    ],
    ids=["타임스탬프형식", "필수필드누락", "정의외필드", "확인필요인데환산날짜"],
)
def test_receive_rejects_general_schema_violations(mutate):
    data = _ex("action-candidate.json")
    mutate(data)
    with pytest.raises(SchemaValidationError):
        receive_agent_output("action-candidate", data)


def test_receive_rejects_non_agent_output_schema_names():
    """Action(저장 형태)은 Agent 출력이 아니다."""
    with pytest.raises(ValueError):
        receive_agent_output("action", _ex("action.json"))


# =============== 저장 ===============

@pytest.fixture
def stored(store_path, new_action):
    """A-001이 정상 저장된 상태."""
    store = ActionStore(store_path)
    store.create(new_action)
    return store


def _second_action(new_action, **changes):
    second = copy.deepcopy(new_action)
    second["action_id"] = "A-002"
    second.update(changes)
    return second


@pytest.mark.parametrize(
    "changes",
    [
        {"memo": "정의 외 필드"},
        {"due_date": "미정"},  # 미정인데 환산 날짜(2026-11-12)가 남아 있음
        {"timestamp": "12:61:00"},
        {"flags": ["없는플래그"]},
    ],
    ids=["정의외필드", "미정인데환산날짜", "타임스탬프형식", "정의외플래그"],
)
def test_create_rejects_schema_violation_and_keeps_existing_file(stored, store_path, new_action, changes):
    before = store_path.read_bytes()
    with pytest.raises(SchemaValidationError):
        stored.create(_second_action(new_action, **changes))
    assert store_path.read_bytes() == before


def test_create_schema_violation_creates_no_file_when_store_is_empty(store_path, new_action):
    new_action["memo"] = "정의 외 필드"
    with pytest.raises(SchemaValidationError):
        ActionStore(store_path).create(new_action)
    assert not store_path.exists()


def test_create_domain_guard_takes_priority_over_schema_violation(store_path, new_action):
    """status 완료(전이표 위반)와 스키마 위반이 함께 있으면 TransitionError (도메인 우선)."""
    new_action.update(status="완료", memo="정의 외 필드")
    with pytest.raises(TransitionError):
        ActionStore(store_path).create(new_action)


def test_change_status_rejects_schema_invalid_evidence_and_keeps_file(stored, store_path):
    bad_evidence = [{"meeting_id": "20261117-01", "timestamp": "00:08:31", "text": "화자 필드 누락"}]
    before = store_path.read_bytes()
    with pytest.raises(SchemaValidationError):
        stored.change_status("A-001", Status.DONE_CANDIDATE, AGENT, evidence=bad_evidence)
    assert store_path.read_bytes() == before


def test_change_status_domain_guard_still_takes_priority(stored, store_path):
    before = store_path.read_bytes()
    with pytest.raises(TransitionError):
        stored.change_status("A-001", Status.DONE, AGENT, evidence=[{"bad": "schema"}])
    assert store_path.read_bytes() == before


# =============== 로드 (직접 편집) ===============

def _tamper(store_path, mutate):
    data = json.loads(store_path.read_text(encoding="utf-8"))
    mutate(data[0])
    store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(memo="직접 편집으로 추가한 필드"),          # additionalProperties
        lambda a: a.update(timestamp="12:61:00"),
        lambda a: a.update(due_date="미정"),
        lambda a: a.pop("evidence"),
        lambda a: a["history"][0].update(memo="이력에 정의 외 필드"),
        lambda a: a.update(flags=["없는플래그"]),
    ],
    ids=["정의외필드", "타임스탬프형식", "미정인데환산날짜", "필수필드누락", "이력정의외필드", "정의외플래그"],
)
def test_load_rejects_schema_violations_from_direct_edit(stored, store_path, mutate):
    _tamper(store_path, mutate)
    with pytest.raises(SchemaValidationError):
        ActionStore(store_path).load_all()


def test_get_also_rejects_schema_violation(stored, store_path):
    _tamper(store_path, lambda a: a.update(memo="x"))
    with pytest.raises(SchemaValidationError):
        ActionStore(store_path).get("A-001")


def test_load_guard_error_takes_priority_over_schema_violation(stored, store_path):
    """완료 + 승인 없음(가드)과 정의 외 필드(스키마)가 함께 있으면 CompletionGuardError."""
    _tamper(store_path, lambda a: a.update(status="완료", approved_by_human=None, memo="x"))
    with pytest.raises(CompletionGuardError):
        ActionStore(store_path).load_all()


def test_load_transition_error_takes_priority_over_schema_violation(store_path, new_action):
    new_action["history"][0]["actor"] = "사람"  # 최초 생성 규칙 위반(도메인) — 스키마도 위반
    new_action["memo"] = "x"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps([new_action], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(TransitionError):
        ActionStore(store_path).load_all()
