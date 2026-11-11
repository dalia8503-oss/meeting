"""T-03-3 / T-03-13 범위: 자기 전이·재판정·완료 철회·재완료·최초 생성 주체 (SPEC 5.2, 5.6, 2026-11-11 결정).

TEST 단계: 구현 전이므로 FAIL이 정상이다 (기능 미구현).

가정 (data-model 8장 #14, #15 미결): 재판정·철회의 입력 누락(근거·사유·철회자 이름)은 ValueError로 가정한다.
"""
import json

import pytest

from meeting_action_agent.state import Actor, CompletionGuardError, Status, TransitionError
from meeting_action_agent.store import ActionStore

AGENT, HUMAN = Actor.AGENT, Actor.HUMAN
_AT = "2026-11-17T10:00:00+09:00"
_OLD_APPROVAL = {
    "approver": "홍길동", "approved_at": _AT,
    "evidence": {"meeting_id": "20261117-01", "timestamp": "00:08:31"},
}


def _seed(store_path, action):
    """사전 상태를 파일로 직접 심는다 (구현 대상에 의존하지 않기 위함)."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps([action], ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def store(store_path, new_action):
    _seed(store_path, new_action)
    return ActionStore(store_path)


@pytest.fixture
def candidate_store(store_path, new_action, followup_evidence):
    new_action["status"] = "완료 후보"
    new_action["history"].append({
        "at": "2026-11-17T09:40:00+09:00", "actor": "Agent",
        "from_status": "신규", "to_status": "완료 후보", "evidence": followup_evidence,
    })
    _seed(store_path, new_action)
    return ActionStore(store_path)


@pytest.fixture
def done_store(store_path, new_action, followup_evidence):
    """사람이 승인해 '완료'가 된 상태."""
    new_action["status"] = "완료"
    new_action["approved_by_human"] = dict(_OLD_APPROVAL)
    new_action["history"] += [
        {"at": "2026-11-17T09:40:00+09:00", "actor": "Agent", "from_status": "신규",
         "to_status": "완료 후보", "evidence": followup_evidence},
        {"at": _AT, "actor": "사람", "actor_name": "홍길동", "from_status": "완료 후보",
         "to_status": "완료", "approval": dict(_OLD_APPROVAL)},
    ]
    _seed(store_path, new_action)
    return ActionStore(store_path)


# ---------- 자기 전이는 change_status가 거부한다 ----------

@pytest.mark.parametrize("status", [Status.NEW, Status.NEEDS_CHECK], ids=lambda s: s.value)
def test_change_status_rejects_self_transition_and_keeps_file(store, store_path, status):
    if status is not Status.NEW:
        store.change_status("A-001", status, AGENT)
    before = store_path.read_bytes()
    with pytest.raises(TransitionError):
        store.change_status("A-001", status, AGENT)
    assert store_path.read_bytes() == before


# ---------- 재판정: 상태 유지 + history ----------

def test_reassessment_keeps_status_and_appends_history(store, followup_evidence):
    store.change_status("A-001", Status.IN_PROGRESS, AGENT)
    n = len(store.get("A-001")["history"])

    store.record_reassessment("A-001", evidence=followup_evidence, note="신규 회의에서도 진행 중 발언")

    saved = store.get("A-001")
    assert saved["status"] == "진행중"
    assert len(saved["history"]) == n + 1
    last = saved["history"][-1]
    assert last["kind"] == "재판정"
    assert last["actor"] == "Agent"
    assert last["from_status"] == last["to_status"] == "진행중"
    assert last["evidence"] == followup_evidence
    assert last["note"] == "신규 회의에서도 진행 중 발언"


def test_agent_reassessment_on_done_candidate_does_not_change_state(candidate_store, followup_evidence):
    """완료 후보는 사람의 판단 대기: Agent는 근거만 기록하고 상태·승인값을 바꾸지 않는다."""
    contrary = [dict(followup_evidence[0], text="아직 마무리 못 했습니다.")]
    candidate_store.record_reassessment("A-001", evidence=contrary, note="상충 발언 발견, 상태 유지")

    saved = candidate_store.get("A-001")
    assert saved["status"] == "완료 후보"
    assert saved["approved_by_human"] is None
    last = saved["history"][-1]
    assert (last["kind"], last["from_status"], last["to_status"]) == ("재판정", "완료 후보", "완료 후보")


def test_reassessment_keeps_earlier_history_untouched(candidate_store, followup_evidence):
    before = candidate_store.get("A-001")["history"]
    candidate_store.record_reassessment("A-001", evidence=followup_evidence, note="추가 근거")
    assert candidate_store.get("A-001")["history"][: len(before)] == before


@pytest.mark.parametrize("evidence", [[], None], ids=["근거빈목록", "근거없음"])
def test_reassessment_requires_evidence(store, store_path, evidence):
    before = store_path.read_bytes()
    with pytest.raises(ValueError):
        store.record_reassessment("A-001", evidence=evidence, note="사유")
    assert store_path.read_bytes() == before


def test_reassessment_requires_note(store, store_path, followup_evidence):
    before = store_path.read_bytes()
    with pytest.raises(ValueError):
        store.record_reassessment("A-001", evidence=followup_evidence, note="")
    assert store_path.read_bytes() == before


# ---------- 최초 생성 주체는 Agent뿐 ----------

def test_create_rejects_human_as_first_history_actor(store_path, new_action):
    new_action["history"][0]["actor"] = "사람"
    with pytest.raises((CompletionGuardError, TransitionError)):
        ActionStore(store_path).create(new_action)
    assert not store_path.exists() or ActionStore(store_path).load_all() == []


# ---------- 완료 철회: 사람만, 승인정보·이력 보존 ----------

def test_human_withdrawal_preserves_approval_and_history(done_store, store_path):
    before = done_store.get("A-001")

    done_store.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name="이팀장", note="자료 오류")

    saved = ActionStore(store_path).get("A-001")
    assert saved["status"] == "진행중"
    assert saved["approved_by_human"] == before["approved_by_human"]  # 지우지 않는다
    assert saved["history"][: len(before["history"])] == before["history"]  # 과거 이력 보존
    last = saved["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("사람", "완료", "진행중")
    assert last["actor_name"] == "이팀장"
    assert last["approval"] == before["approved_by_human"]  # 철회되는 승인 스냅샷
    assert last["at"]  # 철회 시점


def test_agent_cannot_withdraw_done(done_store, store_path):
    before = store_path.read_bytes()
    with pytest.raises(TransitionError):
        done_store.change_status("A-001", Status.IN_PROGRESS, AGENT)
    assert store_path.read_bytes() == before


@pytest.mark.parametrize("name", [None, "", "   "], ids=["없음", "빈값", "공백"])
def test_withdrawal_requires_withdrawer_name(done_store, store_path, name):
    before = store_path.read_bytes()
    with pytest.raises(ValueError):
        done_store.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name=name)
    assert store_path.read_bytes() == before


# ---------- 재완료는 새 승인 필요 ----------

def _withdraw_and_reach_candidate(s, followup_evidence):
    s.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name="이팀장")
    s.change_status("A-001", Status.DONE_CANDIDATE, AGENT, evidence=followup_evidence)


def test_recomplete_without_new_approval_is_rejected(done_store, store_path, followup_evidence):
    """철회 뒤 남아 있는 approved_by_human은 승인으로 인정하지 않는다."""
    _withdraw_and_reach_candidate(done_store, followup_evidence)
    assert done_store.get("A-001")["approved_by_human"] is not None  # 이전 승인값이 남아 있다
    before = store_path.read_bytes()
    with pytest.raises(CompletionGuardError):
        done_store.change_status("A-001", Status.DONE, HUMAN)
    assert store_path.read_bytes() == before


def test_recomplete_with_new_approval_keeps_old_approval_in_history(done_store, followup_evidence):
    _withdraw_and_reach_candidate(done_store, followup_evidence)
    new_approval = {
        "approver": "김부장", "approved_at": "2026-11-24T10:00:00+09:00",
        "evidence": {"meeting_id": "20261124-01", "timestamp": "00:05:00"},
    }

    done_store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=new_approval)

    saved = done_store.get("A-001")
    assert saved["status"] == "완료"
    assert saved["approved_by_human"] == new_approval  # 현재(최신) 승인
    snapshots = [h["approval"] for h in saved["history"] if "approval" in h]
    assert _OLD_APPROVAL in snapshots  # 과거 승인은 history에 남는다
    assert new_approval in snapshots


# ---------- 완료 Action은 재판정 대상에서 제외 (data-model 8장 #14, T-03-13) ----------

def test_reassessment_on_done_action_is_rejected(done_store, store_path, followup_evidence):
    before = store_path.read_bytes()
    with pytest.raises(TransitionError):
        done_store.record_reassessment("A-001", evidence=followup_evidence, note="추가 근거")
    assert store_path.read_bytes() == before


# ---------- 철회 후 기존 승인정보를 그대로 다시 제출해도 재완료 불가 (T-03-3) ----------

def test_recomplete_with_the_old_approval_value_is_rejected(done_store, store_path, followup_evidence):
    done_store.change_status("A-001", Status.IN_PROGRESS, HUMAN, actor_name="이팀장")
    done_store.change_status("A-001", Status.DONE_CANDIDATE, AGENT, evidence=followup_evidence)
    before = store_path.read_bytes()
    with pytest.raises(CompletionGuardError):
        done_store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=dict(_OLD_APPROVAL))
    assert store_path.read_bytes() == before


# ---------- T-03-6 보강: 일반 상태 변경·완료 전이의 history 기록 ----------

def test_change_status_records_actor_evidence_note_and_keeps_past(store, followup_evidence):
    before = store.get("A-001")["history"]

    store.change_status("A-001", Status.DONE_CANDIDATE, AGENT, evidence=followup_evidence, note="완료 보고 확인")

    history = store.get("A-001")["history"]
    assert history[: len(before)] == before  # 과거 이력 보존
    last = history[-1]
    assert (last["actor"], last["from_status"], last["to_status"]) == ("Agent", "신규", "완료 후보")
    assert last["evidence"] == followup_evidence and last["note"] == "완료 보고 확인" and last["at"]


def test_completion_entry_records_approval_snapshot(candidate_store, approval):
    candidate_store.change_status("A-001", Status.DONE, HUMAN, approved_by_human=approval, actor_name="홍길동")

    last = candidate_store.get("A-001")["history"][-1]
    assert last["approval"] == approval and last["actor_name"] == "홍길동"
