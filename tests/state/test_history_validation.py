"""T-03-14: 로드 시 history 전이 검증 (SPEC 9장, 2026-11-11 결정) + 이전 승인 재사용 금지 (SPEC US-05).

TEST 단계: 구현 전에 먼저 작성한다 (RED 확인 후 구현).

규칙
- history의 모든 상태 변경 항목(kind 없음/'상태변경')은 최초 생성 규칙과 전이표(is_allowed)를 따른다.
- 각 항목의 이전 상태는 직전 상태와 이어지고, 마지막 상태는 Action의 현재 status와 같다.
- 재판정 항목(kind='재판정')은 상태 전이가 아니므로 위 검증과 구분한다. 이전·이후 상태가 같고
  그 시점의 현재 상태와 일치해야 한다.
- 예외: 허용되지 않은 전이 → TransitionError, 완료 승인 불일치 → CompletionGuardError.
"""
import json

import pytest

from meeting_action_agent.state import Actor, CompletionGuardError, Status, TransitionError
from meeting_action_agent.store import ActionStore

AGENT, HUMAN = Actor.AGENT, Actor.HUMAN
_AT = "2026-11-17T10:00:00+09:00"


def _entry(frm, to, actor="Agent", **extra):
    return {"at": _AT, "actor": actor, "from_status": frm, "to_status": to, **extra}


def _write(store_path, action, status, history):
    action["status"] = status
    action["history"] = history
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps([action], ensure_ascii=False, indent=2), encoding="utf-8")
    return ActionStore(store_path)


# ---------- 정상: 이어지는 유효 이력은 로드된다 ----------

def test_load_accepts_valid_chain(store_path, new_action):
    s = _write(store_path, new_action, "완료 후보", [
        _entry(None, "신규"), _entry("신규", "진행중"), _entry("진행중", "완료 후보"),
    ])
    assert s.load_all()[0]["status"] == "완료 후보"


def test_load_accepts_reassessment_between_changes(store_path, new_action, followup_evidence):
    s = _write(store_path, new_action, "진행중", [
        _entry(None, "신규"),
        _entry("신규", "진행중"),
        _entry("진행중", "진행중", kind="재판정", evidence=followup_evidence, note="추가 근거"),
    ])
    assert s.load_all()[0]["status"] == "진행중"


# ---------- 최초 생성 규칙 ----------

@pytest.mark.parametrize(
    "first",
    [
        _entry("신규", "진행중"),          # 최초 항목의 이전 상태가 null이 아님
        _entry(None, "신규", actor="사람"),  # 사람의 직접 생성
        _entry(None, "진행중"),             # (없음)→진행중은 전이표에 없음
        _entry(None, "완료 후보"),
    ],
    ids=["이전상태있음", "사람생성", "없음_진행중", "없음_완료후보"],
)
def test_load_rejects_invalid_first_entry(store_path, new_action, first):
    s = _write(store_path, new_action, first["to_status"], [first])
    with pytest.raises(TransitionError):
        s.load_all()


def test_load_rejects_empty_history(store_path, new_action):
    s = _write(store_path, new_action, "신규", [])
    with pytest.raises(TransitionError):
        s.load_all()


# ---------- 전이표 위반 ----------

@pytest.mark.parametrize(
    "bad",
    [
        _entry("신규", "완료 후보", actor="사람"),  # 완료 후보로의 전이는 Agent만
        _entry("진행중", "신규"),                   # 신규로 되돌아가는 전이는 표에 없음
    ],
    ids=["사람의_완료후보", "진행중_신규"],
)
def test_load_rejects_transition_not_in_table(store_path, new_action, bad):
    history = [_entry(None, "신규")]
    if bad["from_status"] != "신규":
        history.append(_entry("신규", "진행중"))
    history.append(bad)
    s = _write(store_path, new_action, bad["to_status"], history)
    with pytest.raises(TransitionError):
        s.load_all()


def test_load_rejects_agent_changing_done_candidate(store_path, new_action):
    s = _write(store_path, new_action, "진행중", [
        _entry(None, "신규"), _entry("신규", "완료 후보"), _entry("완료 후보", "진행중"),
    ])
    with pytest.raises(TransitionError):
        s.load_all()


# ---------- 이어짐·현재 상태와의 일치 ----------

def test_load_rejects_history_gap(store_path, new_action):
    s = _write(store_path, new_action, "미완료", [_entry(None, "신규"), _entry("진행중", "미완료")])
    with pytest.raises(TransitionError):
        s.load_all()


def test_load_rejects_status_that_differs_from_last_history_state(store_path, new_action):
    """history는 신규에서 끝나는데 status만 직접 진행중으로 편집한 경우."""
    s = _write(store_path, new_action, "진행중", [_entry(None, "신규")])
    with pytest.raises(TransitionError):
        s.load_all()


# ---------- kind 구분: 상태변경 vs 재판정 ----------

def test_load_rejects_self_transition_with_explicit_state_change_kind(store_path, new_action):
    s = _write(store_path, new_action, "신규", [
        _entry(None, "신규"), _entry("신규", "신규", kind="상태변경"),
    ])
    with pytest.raises(TransitionError):
        s.load_all()


def test_load_rejects_reassessment_that_changes_state(store_path, new_action, followup_evidence):
    s = _write(store_path, new_action, "진행중", [
        _entry(None, "신규"),
        _entry("신규", "진행중", kind="재판정", evidence=followup_evidence, note="상태를 바꾸는 재판정"),
    ])
    with pytest.raises(TransitionError):
        s.load_all()


def test_load_rejects_reassessment_that_does_not_match_current_state(store_path, new_action, followup_evidence):
    s = _write(store_path, new_action, "신규", [
        _entry(None, "신규"),
        _entry("진행중", "진행중", kind="재판정", evidence=followup_evidence, note="현재 상태와 다름"),
    ])
    with pytest.raises(TransitionError):
        s.load_all()


# ---------- 이전 승인 재사용 금지 (SPEC US-05) ----------

def _approval(who, at):
    return {"approver": who, "approved_at": at,
            "evidence": {"meeting_id": "20261117-01", "timestamp": "00:08:31"}}


def test_recomplete_cannot_reuse_an_earlier_approval_kept_only_in_history(
    store_path, new_action, followup_evidence
):
    """승인 A → 철회 → 승인 B → 철회 → 다시 A를 제출: 현재 값(B)과 달라도 history의 이전 승인 A와 같으면 거부."""
    a = _approval("홍길동", "2026-11-17T10:00:00+09:00")
    b = _approval("김부장", "2026-11-24T10:00:00+09:00")
    store = ActionStore(store_path)
    store.create(new_action)

    def to_candidate():
        store.change_status("A-001", Status.DONE_CANDIDATE, AGENT, evidence=followup_evidence)

    to_candidate()
    store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=a)
    store.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name="이팀장")
    to_candidate()
    store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=b)
    store.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name="이팀장")
    to_candidate()

    before = store_path.read_bytes()
    with pytest.raises(CompletionGuardError):
        store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=dict(a))
    assert store_path.read_bytes() == before
