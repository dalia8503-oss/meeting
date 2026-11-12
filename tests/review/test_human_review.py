"""T-10: Human Review (사람 최종 판단). 기존 ActionStore.change_status 가드를 재사용하는 얇은 domain 함수 검증.

LLM 호출 없음 (순수 deterministic).
"""
import json

import pytest

from meeting_action_agent.review import CHOICES, review_action, withdraw_completion
from meeting_action_agent.state import Actor, CompletionGuardError, Status, TransitionError
from meeting_action_agent.store import ActionStore

AT = "2026-11-17T10:00:00+09:00"
AT2 = "2026-11-18T10:00:00+09:00"
MID, TS = "20261117-01", "00:08:31"


def _seed(path, action):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([action], ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def new_store(store_path, new_action):
    _seed(store_path, new_action)
    return ActionStore(store_path)


@pytest.fixture
def cand(store_path, new_action, followup_evidence):
    new_action["status"] = "완료 후보"
    new_action["history"].append({
        "at": "2026-11-17T09:40:00+09:00", "actor": "Agent",
        "from_status": "신규", "to_status": "완료 후보", "evidence": followup_evidence,
    })
    _seed(store_path, new_action)
    return ActionStore(store_path)


def _done(store, name="홍길동", at=AT, ts=TS):
    review_action(store, "A-001", "완료", actor_name=name, approved_at=at, meeting_id=MID, timestamp=ts)


def test_choices_are_exactly_three():
    assert tuple(CHOICES) == ("완료", "계속 진행", "확인 필요")


# 1~6
def test_candidate_human_complete(cand, store_path):
    _done(cand)
    saved = ActionStore(store_path).get("A-001")
    assert saved["status"] == "완료"
    assert saved["approved_by_human"] == {
        "approver": "홍길동", "approved_at": AT, "evidence": {"meeting_id": MID, "timestamp": TS}}
    last = saved["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("사람", "완료 후보", "완료")
    assert last["actor_name"] == "홍길동"
    assert last["approval"] == saved["approved_by_human"]  # 승인 snapshot
    assert last["at"]


def test_approval_time_defaults_to_now(cand):
    review_action(cand, "A-001", "완료", actor_name="홍길동", meeting_id=MID, timestamp=TS)
    assert cand.get("A-001")["approved_by_human"]["approved_at"]


# 7, 8
@pytest.mark.parametrize("name", [None, "", "   "])
def test_blank_approver_rejected(cand, store_path, name):
    before = store_path.read_bytes()
    with pytest.raises((ValueError, CompletionGuardError)):
        review_action(cand, "A-001", "완료", actor_name=name, approved_at=AT, meeting_id=MID, timestamp=TS)
    assert store_path.read_bytes() == before


@pytest.mark.parametrize("mid,ts", [(None, TS), (MID, None), ("", TS), (MID, " ")])
def test_complete_requires_evidence(cand, store_path, mid, ts):
    before = store_path.read_bytes()
    with pytest.raises((ValueError, CompletionGuardError)):
        review_action(cand, "A-001", "완료", actor_name="홍길동", approved_at=AT, meeting_id=mid, timestamp=ts)
    assert store_path.read_bytes() == before


# 9~12
@pytest.mark.parametrize("src", [Status.NEW, Status.IN_PROGRESS, Status.NOT_DONE, Status.NEEDS_CHECK],
                         ids=lambda s: s.value)
def test_complete_from_non_candidate_rejected(new_store, store_path, src):
    if src is not Status.NEW:
        new_store.change_status("A-001", src, Actor.AGENT)
    before = store_path.read_bytes()
    with pytest.raises((TransitionError, CompletionGuardError)):
        _done(new_store)
    assert store_path.read_bytes() == before


# 13
def test_agent_cannot_complete(cand, approval, store_path):
    before = store_path.read_bytes()
    with pytest.raises((TransitionError, CompletionGuardError)):
        cand.change_status("A-001", Status.DONE, Actor.AGENT, approved_by_human=approval)
    assert store_path.read_bytes() == before


# 14~16
@pytest.mark.parametrize("choice,status", [("계속 진행", "진행중"), ("확인 필요", "확인 필요")])
def test_other_choices(cand, store_path, choice, status):
    review_action(cand, "A-001", choice, actor_name="홍길동")
    saved = ActionStore(store_path).get("A-001")
    assert saved["status"] == status
    assert saved["approved_by_human"] is None
    last = saved["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("사람", "완료 후보", status)
    assert last["actor_name"] == "홍길동"
    assert choice in last["note"]  # 사람의 선택 기록


@pytest.mark.parametrize("choice", ["계속 진행", "확인 필요"])
@pytest.mark.parametrize("name", [None, "", "  "])
def test_other_choices_require_actor_name(cand, store_path, choice, name):
    before = store_path.read_bytes()
    with pytest.raises(ValueError):
        review_action(cand, "A-001", choice, actor_name=name)
    assert store_path.read_bytes() == before


@pytest.mark.parametrize("choice", ["계속 진행", "확인 필요"])
def test_other_choices_from_non_candidate_rejected(new_store, choice):
    with pytest.raises(TransitionError):
        review_action(new_store, "A-001", choice, actor_name="홍길동")


# 17
def test_already_done_cannot_be_completed_again(cand):
    _done(cand)
    with pytest.raises((TransitionError, CompletionGuardError)):
        _done(cand, name="김철수", at=AT2)


# 18, 20, 23
def test_withdraw_by_human_keeps_snapshot_and_history(cand):
    _done(cand)
    approved = cand.get("A-001")["approved_by_human"]
    past = cand.get("A-001")["history"]
    withdraw_completion(cand, "A-001", actor_name="박팀장")
    saved = cand.get("A-001")
    assert saved["status"] == "진행중"
    assert saved["approved_by_human"] == approved  # 보존
    last = saved["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("사람", "완료", "진행중")
    assert last["actor_name"] == "박팀장"
    assert last["approval"] == approved
    assert last["at"]
    assert saved["history"][:len(past)] == past  # 과거 history 보존


@pytest.mark.parametrize("name", [None, "", "  "])
def test_withdraw_requires_name(cand, name):
    _done(cand)
    with pytest.raises(ValueError):
        withdraw_completion(cand, "A-001", actor_name=name)
    assert cand.get("A-001")["status"] == "완료"


def test_withdraw_non_done_rejected(cand):
    with pytest.raises(TransitionError):
        withdraw_completion(cand, "A-001", actor_name="박팀장")


# 19
def test_agent_cannot_withdraw(cand):
    _done(cand)
    with pytest.raises(TransitionError):
        cand.change_status("A-001", Status.IN_PROGRESS, Actor.AGENT, actor_name="Agent")
    assert cand.get("A-001")["status"] == "완료"


# 21, 22
def test_reapproval_after_withdraw(cand, followup_evidence):
    _done(cand)
    withdraw_completion(cand, "A-001", actor_name="박팀장")
    cand.change_status("A-001", Status.DONE_CANDIDATE, Actor.AGENT, evidence=followup_evidence)
    with pytest.raises(CompletionGuardError):
        _done(cand)  # 이전과 동일한 승인자·시각·근거 → 재사용 불가
    assert cand.get("A-001")["status"] == "완료 후보"
    _done(cand, name="김철수", at=AT2, ts="00:10:00")
    saved = cand.get("A-001")
    assert saved["status"] == "완료"
    assert saved["approved_by_human"]["approver"] == "김철수"
    snaps = [e["approval"] for e in saved["history"] if e["to_status"] == "완료"]
    assert [s["approver"] for s in snaps] == ["홍길동", "김철수"]  # 과거 승인 보존


# 24
def test_reload_consistency(cand, store_path):
    _done(cand)
    withdraw_completion(cand, "A-001", actor_name="박팀장")
    assert len(ActionStore(store_path).load_all()) == 1


# 25
@pytest.mark.parametrize("choice", ["", "진행", "done", None, "완료함", "일괄 완료"])
def test_invalid_choice_rejected(cand, store_path, choice):
    before = store_path.read_bytes()
    with pytest.raises(ValueError):
        review_action(cand, "A-001", choice, actor_name="홍길동", approved_at=AT, meeting_id=MID, timestamp=TS)
    assert store_path.read_bytes() == before
