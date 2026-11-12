"""T-BRIEF-INPUT: BriefingInput builder (결정적, LLM/TTS/API 없음)."""
import copy
import hashlib
import json

import pytest

from meeting_action_agent.briefing import BriefingInputError, build_briefing_input
from meeting_action_agent.state import CompletionGuardError
from meeting_action_agent.store import ActionStore

MID = "20261110-01"
TODAY = "2026-11-12"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "meetings").mkdir()
    meta = {"meeting_id": MID, "meeting_date": "2026-11-10", "title": "1차 회의"}
    (tmp_path / "meetings" / f"{MID}.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    analysis = {
        "meeting_id": MID,
        "key_discussions": [{"text": "도면 수정 논의", "evidence_timestamps": ["00:01:00"]}],
        "decisions": [{"text": "오분류 가능 결정", "evidence_timestamps": ["00:02:00"]}],
        "open_issues": [{"text": "품질 문제 추가 검토", "evidence_timestamps": ["00:03:00"]}],
        "needs_confirmation": [
            {"text": "담당자 불명확", "evidence_timestamps": ["00:04:00"]},
            {"text": "담당자  불명확", "evidence_timestamps": ["00:04:00"]},
        ],
        "action_candidates": [],
    }
    (tmp_path / "meetings" / f"{MID}.analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(root):
    return ActionStore(root / "actions" / "actions.json")


@pytest.fixture
def add(store, new_action):
    def _add(action_id, *, status="신규", due="금요일까지", norm="2026-11-13", assignee="김대리",
             task=None, meeting_id=MID):
        a = copy.deepcopy(new_action)
        a.update(action_id=action_id, meeting_id=meeting_id, assignee=assignee, task=task or f"업무 {action_id}",
                 due_date=due, due_date_normalized=norm, status=status)
        a["history"][0]["to_status"] = status
        store.create(a)
        return action_id
    return _add


def build(root, mid=MID, today=TODAY):
    return build_briefing_input(mid, current_date=today, data_root=root)


def by_id(result):
    return {a["action_id"]: a for a in result["active_actions"]}


def test_previous_meeting_explicit(root):
    r = build(root)
    assert r["previous_meeting"] == {"meeting_id": MID, "title": "1차 회의", "meeting_date": "2026-11-10"}
    assert r["current_date"] == TODAY


def test_key_discussions_and_open_issues_kept_with_evidence(root):
    r = build(root)
    assert r["key_discussions"] == [{"text": "도면 수정 논의", "evidence_timestamps": ["00:01:00"]}]
    assert r["open_issues"] == [{"text": "품질 문제 추가 검토", "evidence_timestamps": ["00:03:00"]}]


def test_decisions_excluded_from_briefing(root):
    d = build(root)["decisions"]
    assert d["include_in_briefing"] is False and len(d["items"]) == 1


def test_uses_current_store_status_not_snapshot(root, store, add):
    add("A-001")  # 분석 직후 신규
    assert by_id(build(root))["A-001"]["status"] == "신규"
    store.change_status("A-001", "완료 후보", "Agent", note="2차 회의 완료 보고")
    item = by_id(build(root))["A-001"]
    assert item["status"] == "완료 후보" and item["awaiting_human_confirmation"] is True


def test_all_active_statuses_included(root, store, add):
    for n in ("A-001", "A-002", "A-003", "A-004"):
        add(n)
    add("A-005", status="확인 필요", assignee="확인 필요")
    store.change_status("A-002", "진행중", "Agent")
    store.change_status("A-003", "완료 후보", "Agent")
    store.change_status("A-004", "미완료", "Agent")
    got = {a["action_id"]: a["status"] for a in build(root)["active_actions"]}
    assert got == {"A-001": "신규", "A-002": "진행중", "A-003": "완료 후보", "A-004": "미완료", "A-005": "확인 필요"}


def test_human_approved_done_is_separated(root, store, add, approval):
    add("A-001")
    add("A-002")
    store.change_status("A-001", "완료 후보", "Agent")
    store.change_status("A-001", "완료", "사람", approved_by_human=approval, actor_name="팀장")
    r = build(root)
    assert [c["action_id"] for c in r["completed_actions"]] == ["A-001"]
    assert r["completed_actions"][0]["approved_by_human"] == approval
    assert [a["action_id"] for a in r["active_actions"]] == ["A-002"]


def test_done_without_approval_is_not_briefed(root, store, add):
    add("A-001")
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw[0]["status"] = "완료"  # 직접 편집 우회
    store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CompletionGuardError):
        build(root)


def test_due_raw_and_normalized_preserved(root, add):
    add("A-001", due="다음 주 화요일까지", norm="2026-11-17")
    item = by_id(build(root))["A-001"]
    assert item["due_date_raw"] == "다음 주 화요일까지" and item["due_date_normalized"] == "2026-11-17"


def test_overdue_true(root, add):
    add("A-001", norm="2026-11-11")
    assert by_id(build(root))["A-001"]["overdue"] is True


def test_same_day_not_overdue(root, add):
    add("A-001", norm=TODAY)
    assert by_id(build(root))["A-001"]["overdue"] is False


@pytest.mark.parametrize("due,norm", [("미정", None), ("금요일까지", None), ("확인 필요", None)])
def test_no_normalized_due_never_overdue(root, add, due, norm):
    add("A-001", due=due, norm=norm)
    assert by_id(build(root))["A-001"]["overdue"] is False


def test_needs_confirmation_collected_and_deduped(root, add):
    add("A-001", status="확인 필요", assignee="확인 필요")
    add("A-002", status="확인 필요", assignee="확인 필요")
    nc = build(root)["needs_confirmation"]
    assert [c["source"] for c in nc] == ["analysis", "action", "action"]
    assert [c.get("action_id") for c in nc[1:]] == ["A-001", "A-002"]
    assert nc[0]["evidence_timestamps"] == ["00:04:00"]


def test_action_evidence_preserved(root, add):
    add("A-001")
    ev = by_id(build(root))["A-001"]["evidence"]
    assert ev["meeting_id"] == MID and ev["timestamp"] == "00:12:31" and ev["text"]


def test_sort_order(root, store, add):
    for n in ("A-001", "A-002", "A-003", "A-004"):
        add(n)
    add("A-005", status="확인 필요", assignee="확인 필요")
    add("A-006", norm="2026-11-01")  # 신규 + 기한 초과
    store.change_status("A-002", "진행중", "Agent")
    store.change_status("A-003", "완료 후보", "Agent")
    store.change_status("A-004", "미완료", "Agent")
    order = [a["action_id"] for a in build(root)["active_actions"]]
    assert order == ["A-005", "A-004", "A-006", "A-002", "A-001", "A-003"]


def test_other_meeting_actions_excluded(root, add):
    add("A-001")
    add("A-002", meeting_id="20261117-01")
    assert list(by_id(build(root))) == ["A-001"]


def test_meeting_without_actions(root):
    r = build(root)
    assert r["active_actions"] == [] and r["completed_actions"] == []


def test_originals_unchanged(root, add):
    add("A-001")

    def snap():
        return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}

    before = snap()
    build(root)
    assert before == snap()


def test_missing_previous_meeting_errors(root):
    with pytest.raises(BriefingInputError):
        build(root, mid="20990101-zz")
    with pytest.raises(BriefingInputError):
        build(root, mid="../evil")


def test_unanalyzed_meeting_errors(root):
    (root / "meetings" / f"{MID}.analysis.json").unlink()
    with pytest.raises(BriefingInputError):
        build(root)


def test_bad_current_date_errors(root):
    with pytest.raises(BriefingInputError):
        build(root, today="내일")
