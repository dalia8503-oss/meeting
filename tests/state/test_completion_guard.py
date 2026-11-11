"""T-03-3: 완료 가드 (저장 계층). SPEC US-05 / 9장, CLAUDE.md 절대 원칙 5.

TEST 단계: 구현 전이므로 FAIL이 정상이다 (기능 미구현).

검증 대상
- approved_by_human 없으면 '완료' 저장 거부
- Agent 출력의 '완료' 거부
- '완료 후보' 이외에서 '완료' 거부
- 저장소 로드 시에도 같은 검증 (파일 직접 편집 우회 차단)
- 거부된 시도는 저장 상태·history를 바꾸지 않는다

인터페이스 근거 대조 (plan.md 4.4, data-model.md 5·6장, contracts/)
  문서에 정의됨:
  - 상태 변경은 store의 단일 진입점만 통과, 전이표·완료 가드·history 기록을 함께 수행 (plan 4.4)
  - 로드 시에도 '완료'에 approved_by_human이 없으면 오류 (plan 4.4)
  - Action/approved_by_human/history/EvidenceQuote 필드 구조 (action.schema.json)
  - 자기 전이·재판정·완료 철회·재완료·최초 생성 주체 정책(SPEC 5.2, 5.6)은 test_history_policy.py
  문서에 없어 테스트용으로 둔 가정 (확정 아님, 구현 승인 시 확인 필요):
  - 클래스·메서드 이름과 시그니처: ActionStore(path), create, change_status(..., actor_name=),
    record_reassessment(action_id, *, evidence, note), get, load_all
  - 저장 파일 형태: 단일 JSON 파일 (data-model 8장 #8 파일 단위 미결과 무관하게 경로만 받음)
  - 예외 이름: CompletionGuardError, TransitionError
"""
import json

import pytest

from meeting_action_agent.state import Actor, CompletionGuardError, Status, TransitionError
from meeting_action_agent.store import ActionStore

AGENT, HUMAN = Actor.AGENT, Actor.HUMAN


def _seed(store_path, action):
    """테스트 사전 상태를 파일로 직접 심는다. 구현 대상(create 등)에 의존하지 않기 위함이며,
    픽스처 단계 에러(ERROR)가 아니라 테스트 본문의 실패(FAIL)로 드러나게 한다.
    저장 형태(단일 JSON 배열)는 테스트용 가정이다."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps([action], ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def store(store_path, new_action):
    _seed(store_path, new_action)
    return ActionStore(store_path)


@pytest.fixture
def candidate_store(store_path, new_action, followup_evidence):
    """A-001이 Agent의 정상 경로로 '완료 후보'가 된 상태."""
    new_action["status"] = "완료 후보"
    new_action["history"].append({
        "at": "2026-11-17T09:40:00+09:00", "actor": "Agent",
        "from_status": "신규", "to_status": "완료 후보", "evidence": followup_evidence,
    })
    _seed(store_path, new_action)
    return ActionStore(store_path)


# ---------- 정상 경로 ----------

def test_human_with_approval_can_complete_from_candidate(candidate_store, approval, store_path):
    candidate_store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=approval)

    saved = ActionStore(store_path).get("A-001")  # 재로드해도 유지
    assert saved["status"] == "완료"
    assert saved["approved_by_human"]["approver"] == approval["approver"]
    last = saved["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("사람", "완료 후보", "완료")


# ---------- 승인값 없는 완료 차단 (US-05) ----------

def test_complete_without_approval_is_rejected_and_nothing_changes(candidate_store, store_path):
    before = store_path.read_bytes()
    with pytest.raises(CompletionGuardError):
        candidate_store.change_status("A-001", Status.DONE, HUMAN)
    assert store_path.read_bytes() == before
    assert candidate_store.get("A-001")["status"] == "완료 후보"


_EV = {"meeting_id": "20261117-01", "timestamp": "00:08:31"}
_AT = "2026-11-17T10:00:00+09:00"


@pytest.mark.parametrize(
    "bad",
    [
        {"approver": "", "approved_at": _AT, "evidence": _EV},
        {"approver": "홍길동", "approved_at": _AT},  # evidence 누락
        {},
    ],
    # 공백만 있는 승인자(CHK047)는 contracts(minLength 1)가 거부하지 않으므로 제외 (SPEC 수정 제안 대상)
    ids=["승인자빈값", "근거누락", "빈객체"],
)
def test_complete_with_invalid_approval_is_rejected(candidate_store, store_path, bad):
    before = store_path.read_bytes()
    with pytest.raises(CompletionGuardError):
        candidate_store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=bad)
    assert store_path.read_bytes() == before


# ---------- Agent 출력의 완료 거부 (US-04/05, 9장) ----------

def test_agent_cannot_set_done_even_with_approval_value(candidate_store, approval, store_path):
    before = store_path.read_bytes()
    with pytest.raises((CompletionGuardError, TransitionError)):
        candidate_store.change_status("A-001", Status.DONE, AGENT, approved_by_human=approval)
    assert store_path.read_bytes() == before


def test_create_with_status_done_is_rejected(store_path, new_action, approval):
    new_action["status"] = "완료"
    new_action["approved_by_human"] = approval
    with pytest.raises((CompletionGuardError, TransitionError)):
        ActionStore(store_path).create(new_action)
    assert not store_path.exists() or ActionStore(store_path).load_all() == []


# ---------- '완료 후보' 이외에서 완료 거부 (5.6) ----------

@pytest.mark.parametrize(
    "src", [Status.NEW, Status.IN_PROGRESS, Status.NOT_DONE, Status.NEEDS_CHECK],
    ids=lambda s: s.value,
)
def test_complete_from_non_candidate_is_rejected_even_for_human_with_approval(
    store, approval, store_path, src
):
    if src is not Status.NEW:
        store.change_status("A-001", src, AGENT)
    before = store_path.read_bytes()
    with pytest.raises((CompletionGuardError, TransitionError)):
        store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=approval)
    assert store_path.read_bytes() == before
    assert store.get("A-001")["status"] == src.value


def test_agent_cannot_move_candidate_to_other_agent_states(candidate_store):
    """5.6 표: 완료 후보에서의 전이는 사람만 (Agent 전이의 출발 상태에 완료 후보 없음)."""
    with pytest.raises(TransitionError):
        candidate_store.change_status("A-001", Status.NEW, AGENT)


# ---------- 로드 시 재검증: 파일 직접 편집 우회 차단 (US-05 실패 조건) ----------

def _tamper(store_path, mutate):
    data = json.loads(store_path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("actions", data)
    mutate(items[0])
    store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_rejects_file_edited_to_done_without_approval(candidate_store, store_path):
    _tamper(store_path, lambda a: a.update(status="완료", approved_by_human=None))
    with pytest.raises(CompletionGuardError):
        ActionStore(store_path).load_all()


# 로드 시 정합성 검증 (2026-11-11 결정: 완료 전이 approval ↔ approved_by_human, kind 없는 자기 전이 거부)

def test_load_rejects_done_without_human_done_transition_in_history(candidate_store, approval, store_path):
    """승인값을 직접 써 넣어도, 사람이 완료 후보→완료로 옮긴 history(승인 스냅샷)가 없으면 거부."""
    _tamper(store_path, lambda a: a.update(status="완료", approved_by_human=approval))
    with pytest.raises(CompletionGuardError):
        ActionStore(store_path).load_all()


def test_load_rejects_done_when_history_transition_was_not_from_done_candidate(store, approval, store_path):
    def mutate(a):
        a["status"] = "완료"
        a["approved_by_human"] = approval
        a["history"].append({
            "at": _AT, "actor": "사람", "from_status": "신규", "to_status": "완료", "approval": approval,
        })

    _tamper(store_path, mutate)
    with pytest.raises(CompletionGuardError):
        ActionStore(store_path).load_all()


def test_load_rejects_done_when_snapshot_differs_from_approved_by_human(candidate_store, approval, store_path):
    def mutate(a):
        a["status"] = "완료"
        a["approved_by_human"] = dict(approval, approver="다른사람")
        a["history"].append({
            "at": _AT, "actor": "사람", "from_status": "완료 후보", "to_status": "완료", "approval": approval,
        })

    _tamper(store_path, mutate)
    with pytest.raises(CompletionGuardError):
        ActionStore(store_path).load_all()


def test_load_accepts_consistent_done(candidate_store, approval, store_path):
    def mutate(a):
        a["status"] = "완료"
        a["approved_by_human"] = approval
        a["history"].append({
            "at": _AT, "actor": "사람", "from_status": "완료 후보", "to_status": "완료", "approval": approval,
        })

    _tamper(store_path, mutate)
    assert ActionStore(store_path).load_all()[0]["status"] == "완료"


def test_load_rejects_history_self_transition_without_kind(candidate_store, store_path):
    """kind 없이 from_status == to_status인 이력은 상태 전이가 아니므로 거부."""
    _tamper(store_path, lambda a: a["history"].append(
        {"at": _AT, "actor": "Agent", "from_status": "완료 후보", "to_status": "완료 후보"}))
    with pytest.raises(TransitionError):
        ActionStore(store_path).load_all()


def test_load_accepts_reassessment_entry_with_kind(candidate_store, followup_evidence, store_path):
    _tamper(store_path, lambda a: a["history"].append(
        {"at": _AT, "actor": "Agent", "kind": "재판정", "from_status": "완료 후보",
         "to_status": "완료 후보", "evidence": followup_evidence, "note": "추가 근거"}))
    assert ActionStore(store_path).load_all()[0]["status"] == "완료 후보"


# ---------- 거부된 시도는 이력도 남기지 않는다 ----------

def test_rejected_attempt_does_not_append_history(candidate_store):
    n = len(candidate_store.get("A-001")["history"])
    with pytest.raises(CompletionGuardError):
        candidate_store.change_status("A-001", Status.DONE, HUMAN)
    assert len(candidate_store.get("A-001")["history"]) == n
