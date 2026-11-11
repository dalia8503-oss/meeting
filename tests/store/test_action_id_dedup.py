"""T-03-5 범위: action_id 발급, 같은 회의 중복(extra_evidence), 이전 Action 재언급.

판정 기준은 data-model 2.3 불변 규칙만 사용한다: '동일 담당자·업무'(assignee+task 일치).
TEST 단계: 구현 전이므로 FAIL이 정상이다 (기능 미구현).
"""
import copy

from meeting_action_agent.store import ActionStore


def _action(base, action_id, meeting_id="20261110-01", assignee="민수", task="생산성 자료 재확인"):
    a = copy.deepcopy(base)
    a.update(action_id=action_id, meeting_id=meeting_id, assignee=assignee, task=task)
    return a


def _candidate(meeting_id="20261110-01", assignee="민수", task="생산성 자료 재확인"):
    return {
        "meeting_id": meeting_id, "assigner": "팀장", "assignee": assignee, "task": task,
        "due_date": "미정", "due_date_normalized": None,
        "evidence": "민수 씨 그거 꼭 해주세요.", "timestamp": "00:20:00",
        "status": "신규", "flags": [],
    }


def _quote(meeting_id="20261110-01"):
    return {"meeting_id": meeting_id, "timestamp": "00:20:00", "speaker": "팀장", "text": "민수 씨 그거 꼭 해주세요."}


def test_action_id_sequential_and_unique(store_path, new_action):
    store = ActionStore(store_path)
    assert store.next_action_id() == "A-001"
    for i in (1, 2, 3):
        store.create(_action(new_action, store.next_action_id(), task=f"업무{i}"))
    ids = [a["action_id"] for a in store.load_all()]
    assert ids == ["A-001", "A-002", "A-003"]
    assert store.next_action_id() == "A-004"


def test_next_id_continues_after_reload(store_path, new_action):
    store = ActionStore(store_path)
    store.create(_action(new_action, "A-001"))
    store.create(_action(new_action, "A-007", task="다른 업무"))
    assert ActionStore(store_path).next_action_id() == "A-008"  # 새 인스턴스 = 재로드


def test_same_meeting_duplicate_is_not_new(store_path, new_action):
    store = ActionStore(store_path)
    store.create(_action(new_action, "A-001"))
    res = store.resolve_candidate(_candidate())
    assert res.kind == "duplicate" and res.action_id == "A-001"


def test_duplicate_evidence_kept_in_extra_evidence(store_path, new_action):
    store = ActionStore(store_path)
    store.create(_action(new_action, "A-001"))
    original = store.get("A-001")["evidence"]
    store.add_extra_evidence("A-001", [_quote()])
    got = store.get("A-001")
    assert got["extra_evidence"] == [_quote()]
    assert got["evidence"] == original and len(store.load_all()) == 1


def test_previous_meeting_mention_is_not_new(store_path, new_action):
    store = ActionStore(store_path)
    store.create(_action(new_action, "A-001"))
    res = store.resolve_candidate(_candidate(meeting_id="20261117-01"))
    assert res.kind == "remention" and res.action_id == "A-001"
    assert store.get("A-001")["status"] == "신규" and len(store.load_all()) == 1  # 상태 불변


def test_different_instruction_is_not_merged(store_path, new_action):
    store = ActionStore(store_path)
    store.create(_action(new_action, "A-001"))
    for meeting in ("20261110-01", "20261117-01"):
        res = store.resolve_candidate(_candidate(meeting_id=meeting, task="보고서 초안 작성"))
        assert res.kind == "new" and res.action_id is None
