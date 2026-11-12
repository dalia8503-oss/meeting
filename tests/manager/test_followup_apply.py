"""T-09 검증된 FollowupResult → Action 상태 반영. 외부 LLM 없이 fake Reviewer runner로 검증한다."""
import copy
import json

import pytest

from meeting_action_agent.contracts import validate
from meeting_action_agent.manager.followup import FollowupValidationError
from meeting_action_agent.manager.followup_apply import (
    apply_followup,
    apply_followups,
    is_overdue,
)
from meeting_action_agent.reviewer import ReviewOutputError
from meeting_action_agent.state import CompletionGuardError, TransitionError
from meeting_action_agent.store import ActionStore

PREV = "20261110-01"
CUR = "20261117-01"
CUR_DATE = "2026-11-17"
TS = "00:08:31"
DONE_TEXT = "도면 수정해서 공유했습니다."
NOT_STARTED = "도면 수정은 아직 시작하지 못했습니다."
WORKING = "도면 수정 작업 중입니다."


def _action(base, action_id="A-001", *, assignee="김대리", status="신규", due="금요일", due_norm="2026-11-13"):
    a = copy.deepcopy(base)
    a.update(action_id=action_id, meeting_id=PREV, assignee=assignee, task="도면 수정해서 공유",
             due_date=due, due_date_normalized=due_norm, status=status)
    a["history"][0]["to_status"] = status
    return a


def _tr(text, speaker="김대리"):
    return [{"meeting_id": CUR, "speaker": speaker, "timestamp": TS, "text": text}]


def _result(text, status, conf="상", action_id="A-001", flags=(), proposed_due=None, evidence=True):
    r = {"meeting_id": CUR, "action_id": action_id, "proposed_status": status, "confidence": conf,
         "evidence": [{"meeting_id": CUR, "timestamp": TS, "speaker": "김대리", "text": text}] if evidence else [],
         "flags_to_add": list(flags), "reason": "테스트 판정"}
    if proposed_due:
        r["proposed_due_date"] = proposed_due
    return r


def pass_runner(calls=None):
    def runner(prompt):
        if calls is not None:
            calls.append(prompt)
        ref = json.JSONDecoder().raw_decode(prompt.split("## INPUT\n", 1)[1])[0]["followup_result"]["action_id"]
        return json.dumps({"target": {"type": "followup", "ref": ref}, "verdict": "PASS", "reasons": [], "attempt": 0})
    return runner


def reject_runner(prompt):
    ref = json.JSONDecoder().raw_decode(prompt.split("## INPUT\n", 1)[1])[0]["followup_result"]["action_id"]
    return json.dumps({"target": {"type": "followup", "ref": ref}, "verdict": "REJECT",
                       "reasons": [{"code": "판정비도출", "detail": "근거와 판정 불일치"}], "attempt": 0})


@pytest.fixture
def make(store_path, new_action):
    store = ActionStore(store_path)

    def _make(**kw):
        steps = kw.pop("steps", ())
        a = _action(new_action, **kw)
        store.create(a)
        for to in steps:
            store.change_status(a["action_id"], to, "Agent")
        return store
    return _make


def _apply(store, text, status, runner=None, date=CUR_DATE, **kw):
    return apply_followup(store, _result(text, status, **kw), _tr(text), runner or pass_runner(), date)


def _snapshot(store):
    return json.dumps(store.load_all(), ensure_ascii=False, sort_keys=True)


# 1. 완료 후보 PASS → 완료 후보 (+16 Evidence 보존, 17 history 연속)
def test_done_candidate_pass_applies_and_preserves_evidence(make):
    store = make()
    out = _apply(store, DONE_TEXT, "완료 후보")
    a = store.get("A-001")
    assert (out.outcome, a["status"]) == ("applied", "완료 후보")
    last = a["history"][-1]
    assert (last["from_status"], last["to_status"], last["actor"]) == ("신규", "완료 후보", "Agent")
    assert last["evidence"][0]["text"] == DONE_TEXT and last["evidence"][0]["timestamp"] == TS
    assert "확신도=상" in last["note"]
    validate("action", a)


# 2. REJECT → status 불변, 쓰기 없음
def test_reviewer_reject_changes_nothing(make):
    store = make()
    before = _snapshot(store)
    out = _apply(store, DONE_TEXT, "완료 후보", runner=reject_runner)
    assert out.outcome == "rejected" and out.review["verdict"] == "REJECT"
    assert _snapshot(store) == before


# 3, 4. 진행중 / 미완료
def test_in_progress_pass(make):
    store = make()
    _apply(store, WORKING, "진행중", conf="중")
    assert store.get("A-001")["status"] == "진행중"


def test_not_done_pass(make):
    store = make()
    _apply(store, NOT_STARTED, "미완료")
    assert store.get("A-001")["status"] == "미완료"


# 5. 미언급(근거 없음): 어떤 write도 없다 (T-09-7 정책 7: 상태·flags·history 불변)
def test_no_mention_without_evidence_writes_nothing(make):
    store = make()
    before = _snapshot(store)
    out = apply_followup(store, _result("", "확인 필요", conf="하", evidence=False), _tr("무관한 발언"),
                         pass_runner(), CUR_DATE)
    assert out.outcome == "skipped" and _snapshot(store) == before


def test_needs_check_without_evidence_on_needs_check_writes_nothing(make):
    store = make(status="확인 필요")
    before = _snapshot(store)
    out = apply_followup(store, _result("", "확인 필요", conf="하", evidence=False), _tr("무관한 발언"),
                         pass_runner(), CUR_DATE)
    assert out.outcome == "skipped" and _snapshot(store) == before


# 6. Agent 완료 생성 금지 (제안 '완료' 거부 + 완료 후보 PASS도 완료가 아님)
def test_agent_cannot_create_done(make):
    store = make()
    with pytest.raises(CompletionGuardError):
        _apply(store, DONE_TEXT, "완료")
    assert store.get("A-001")["status"] == "신규"
    _apply(store, DONE_TEXT, "완료 후보")
    a = store.get("A-001")
    assert a["status"] == "완료 후보" and a["approved_by_human"] is None
    with pytest.raises(TransitionError):
        store.change_status("A-001", "완료", "Agent")


# 7. 담당자 확인 필요 → 완료 후보 금지 (CHK020)
def test_unknown_assignee_cannot_become_done_candidate(make):
    store = make(assignee="확인 필요", status="확인 필요")
    out = _apply(store, DONE_TEXT, "완료 후보")
    a = store.get("A-001")
    assert a["status"] == "확인 필요" and out.outcome == "reassessed"
    assert "CHK020" in a["history"][-1]["note"] and a["history"][-1]["kind"] == "재판정"


def test_unknown_assignee_with_other_status_goes_to_needs_check_not_candidate(make):
    store = make(assignee="확인 필요", status="확인 필요", steps=("진행중",))
    _apply(store, DONE_TEXT, "완료 후보")
    assert store.get("A-001")["status"] == "확인 필요"


# 8~11. overdue (CHK024)
def test_overdue_same_day_false(new_action):
    assert is_overdue(_action(new_action), "2026-11-13") is False


def test_overdue_next_day_true(new_action):
    assert is_overdue(_action(new_action), "2026-11-14") is True


@pytest.mark.parametrize("due,norm", [("금요일", None), ("미정", None), ("확인 필요", None), ("모레쯤", None)])
def test_overdue_not_inferred_without_normalized(new_action, due, norm):
    assert is_overdue(_action(new_action, due=due, due_norm=norm), CUR_DATE) is False


def test_overdue_invalid_date_is_false(new_action):
    assert is_overdue(_action(new_action, due_norm="not-a-date"), CUR_DATE) is False


def test_overdue_alone_does_not_change_anything(make):
    store = make(due_norm="2026-11-13")  # 현재 회의일 11-17 > 기한 → overdue
    assert is_overdue(store.get("A-001"), CUR_DATE)
    before = _snapshot(store)
    apply_followup(store, _result("", "확인 필요", conf="하", evidence=False), _tr("무관한 발언"),
                   pass_runner(), CUR_DATE)
    assert _snapshot(store) == before  # 미언급+기한경과: status·flags·history 불변 (정책 7)


def test_overdue_with_done_evidence_can_be_candidate_and_notes_overdue(make):
    store = make(due_norm="2026-11-13")
    _apply(store, DONE_TEXT, "완료 후보")
    a = store.get("A-001")
    assert a["status"] == "완료 후보" and "기한경과" in a["history"][-1]["note"]


# 12. 기한 변경 제안 → 기존 due 유지
def test_due_change_proposal_keeps_due_date(make):
    store = make(due_norm="2026-11-13")
    text = "금요일까지는 어렵고 다음주 월요일까지 하겠습니다."
    _apply(store, text, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    a = store.get("A-001")
    assert (a["due_date"], a["due_date_normalized"]) == ("금요일", "2026-11-13")
    assert "기한변경제안: 다음주 월요일" in a["history"][-1]["note"]
    assert "2026-11-16" not in json.dumps(a, ensure_ascii=False)


# 13, 17. 완료 후보 재판정 → status 유지 + reassessment, 반복 재판정
def test_done_candidate_stays_and_records_reassessment(make):
    store = make(steps=("완료 후보",))
    n = len(store.get("A-001")["history"])
    out = _apply(store, NOT_STARTED, "미완료")
    a = store.get("A-001")
    assert out.outcome == "reassessed" and a["status"] == "완료 후보"
    assert len(a["history"]) == n + 1
    last = a["history"][-1]
    assert last["kind"] == "재판정" and last["from_status"] == last["to_status"] == "완료 후보"
    assert last["evidence"][0]["text"] == NOT_STARTED


def test_repeated_reassessment_is_fine_and_history_continues(make):
    store = make(steps=("완료 후보",))
    for text, st in [(NOT_STARTED, "미완료"), (WORKING, "진행중"), (NOT_STARTED, "미완료")]:
        _apply(store, text, st, conf="중")
    a = store.get("A-001")
    assert a["status"] == "완료 후보"
    assert [h.get("kind") for h in a["history"]].count("재판정") == 3
    ActionStore(store.path).load_all()  # history 연속성 검증 통과


def test_same_status_judgement_uses_reassessment(make):
    store = make(steps=("진행중",))
    out = _apply(store, WORKING, "진행중", conf="중")
    assert out.outcome == "reassessed" and store.get("A-001")["history"][-1]["kind"] == "재판정"


# 14. 완료 Action 보호
def test_done_action_is_not_reassessed(make, approval):
    store = make(steps=("완료 후보",))
    store.change_status("A-001", "완료", "사람", approved_by_human=approval)
    before = _snapshot(store)
    calls = []
    out = _apply(store, NOT_STARTED, "미완료", runner=pass_runner(calls))
    assert out.outcome == "skipped" and not calls
    assert _snapshot(store) == before


# 15. Reviewer read-only
def test_reviewer_does_not_mutate_inputs(make):
    store = make()
    result, tr = _result(DONE_TEXT, "완료 후보"), _tr(DONE_TEXT)
    r0, t0 = copy.deepcopy(result), copy.deepcopy(tr)

    def mutating_runner(prompt):  # runner가 받은 복사본을 건드려도 원본에 영향이 없어야 한다
        return pass_runner()(prompt)

    apply_followup(store, result, tr, mutating_runner, CUR_DATE)
    assert result == r0 and tr == t0


def test_reviewer_extra_fields_rejected_fail_closed(make):
    store = make()
    before = _snapshot(store)
    bad = lambda p: json.dumps({"target": {"type": "followup", "ref": "A-001"}, "verdict": "PASS",
                                "reasons": [], "attempt": 0, "fixed_status": "완료"})
    with pytest.raises(ReviewOutputError):
        _apply(store, DONE_TEXT, "완료 후보", runner=bad)
    assert _snapshot(store) == before


# 19. Reviewer 호출 시점에는 상태 변경이 없다
def test_no_state_change_before_reviewer(make):
    store = make()
    seen = []

    def runner(prompt):
        seen.append(store.get("A-001")["status"])
        return pass_runner()(prompt)

    _apply(store, DONE_TEXT, "완료 후보", runner=runner)
    assert seen == ["신규"] and store.get("A-001")["status"] == "완료 후보"


# 20. invalid FollowupResult → write 없음 (+Atomicity: 실패 시 부분 write 없음)
@pytest.mark.parametrize("mutate", [
    lambda r: r["evidence"][0].update(text="없는 발언"),         # 근거가 transcript에 없음
    lambda r: r.update(action_id="A-001", meeting_id="20990101-01"),  # meeting_id 불일치
    lambda r: r.pop("reason"),                                     # 스키마 위반
])
def test_invalid_followup_writes_nothing(make, mutate):
    from meeting_action_agent.contracts import SchemaValidationError
    store = make()
    before = _snapshot(store)
    r = _result(DONE_TEXT, "완료 후보")
    mutate(r)
    calls = []
    with pytest.raises((FollowupValidationError, SchemaValidationError)):
        apply_followup(store, r, _tr(DONE_TEXT), pass_runner(calls), CUR_DATE)
    assert _snapshot(store) == before and not calls  # Reviewer도 호출하지 않는다


def test_question_or_negation_as_done_candidate_is_rejected_without_write(make):
    store = make()
    before = _snapshot(store)
    text = "도면 수정해서 공유했나요?"
    with pytest.raises(FollowupValidationError):
        _apply(store, text, "완료 후보")
    assert _snapshot(store) == before


def test_store_failure_leaves_file_unchanged(make, monkeypatch):
    store = make()
    before = _snapshot(store)

    def boom(self, actions):
        raise OSError("disk")
    monkeypatch.setattr(ActionStore, "_write", boom)
    with pytest.raises(OSError):
        _apply(store, DONE_TEXT, "완료 후보")
    monkeypatch.undo()
    assert _snapshot(store) == before


# 여러 건: 한 건 검증 실패가 다른 건을 막지 않는다
def test_apply_followups_isolates_failures(make, new_action):
    store = make()
    store.create(_action(new_action, "A-002"))
    good = _result(DONE_TEXT, "완료 후보", action_id="A-002")
    bad = _result("없는 발언", "완료 후보", action_id="A-001")
    outs = apply_followups(store, [bad, good], _tr(DONE_TEXT), pass_runner(), CUR_DATE)
    assert [o.outcome for o in outs] == ["rejected", "applied"]
    assert store.get("A-001")["status"] == "신규" and store.get("A-002")["status"] == "완료 후보"


# ===== T-09-5 / T-09-10: flags / flag_notes 부착 =====
PROPOSAL = "금요일까지는 어렵고 다음주 월요일까지 하겠습니다."


def _flags(store, aid="A-001"):
    a = store.get(aid)
    return a["flags"], a.get("flag_notes", {})


def test_not_overdue_has_no_overdue_flag(make):
    store = make(due_norm="2026-11-20")
    _apply(store, WORKING, "진행중", conf="중")
    assert "기한경과" not in store.get("A-001")["flags"]


def test_overdue_attaches_flag_and_note(make):
    store = make(due_norm="2026-11-13")
    _apply(store, WORKING, "진행중", conf="중")
    flags, notes = _flags(store)
    assert "기한경과" in flags and "2026-11-13" in notes["기한경과"]["note"]
    validate("action", store.get("A-001"))


def test_overdue_flag_does_not_change_status_to_not_done(make):
    store = make(due_norm="2026-11-13")
    _apply(store, WORKING, "진행중", conf="중")
    assert store.get("A-001")["status"] == "진행중"


@pytest.mark.parametrize("due,norm", [("금요일", None), ("미정", None), ("확인 필요", None)])
def test_no_overdue_flag_without_usable_due(make, due, norm):
    store = make(due=due, due_norm=norm)
    _apply(store, WORKING, "진행중", conf="중", flags=("기한경과",))  # 결과가 요구해도 부착 금지
    assert "기한경과" not in store.get("A-001")["flags"]


def test_overdue_requested_by_result_but_not_overdue_is_ignored(make):
    store = make(due_norm="2026-11-20")
    _apply(store, WORKING, "진행중", conf="중", flags=("기한경과",))
    assert "기한경과" not in store.get("A-001")["flags"]


def test_due_change_proposal_persists_flag_note_and_keeps_due(make):
    store = make(due_norm="2026-11-13")
    _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    a = store.get("A-001")
    assert "기한변경제안" in a["flags"]
    assert a["flag_notes"]["기한변경제안"]["note"] == "다음주 월요일"
    assert a["flag_notes"]["기한변경제안"]["evidence"][0]["text"] == PROPOSAL
    assert (a["due_date"], a["due_date_normalized"]) == ("금요일", "2026-11-13")
    assert a["status"] == "진행중"
    validate("action", a)


def test_same_flags_not_duplicated_on_repeat(make):
    store = make(due_norm="2026-11-13")
    for _ in range(3):
        _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    a = store.get("A-001")
    assert sorted(a["flags"]) == ["기한경과", "기한변경제안"]
    assert a["flag_notes"]["기한변경제안"]["note"] == "다음주 월요일"
    assert a["flag_notes"]["기한경과"]["note"].count("2026-11-13") == 1
    assert len(a["flag_notes"]["기한변경제안"]["evidence"]) == 1
    assert [h.get("kind") for h in a["history"]].count("재판정") == 2  # history는 매번 남는다


def test_different_meeting_evidence_and_history_preserved(make):
    store = make(due_norm="2026-11-13")
    _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    other = "MEET2"
    text2 = "다음주 월요일은 어렵고 수요일까지 하겠습니다."
    r2 = _result(text2, "진행중", conf="중", flags=("기한변경제안",), proposed_due="수요일")
    r2["meeting_id"] = other
    r2["evidence"][0]["meeting_id"] = other
    tr2 = [{"meeting_id": other, "speaker": "김대리", "timestamp": TS, "text": text2}]
    apply_followup(store, r2, tr2, pass_runner(), "2026-11-24")
    a = store.get("A-001")
    n = a["flag_notes"]["기한변경제안"]
    assert "다음주 월요일" in n["note"] and "수요일" in n["note"]
    assert {e["meeting_id"] for e in n["evidence"]} == {CUR, other}
    assert a["flags"].count("기한변경제안") == 1
    assert [h["evidence"][0]["meeting_id"] for h in a["history"] if h.get("evidence")] == [CUR, other]


def test_done_candidate_status_locked_but_flags_attached(make):
    store = make(steps=("완료 후보",), due_norm="2026-11-13")
    _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    a = store.get("A-001")
    assert a["status"] == "완료 후보"
    assert {"기한경과", "기한변경제안"} <= set(a["flags"])
    assert a["history"][-1]["kind"] == "재판정"  # reassessment와 flag 동시 보존


def test_done_action_gets_no_flags(make, approval):
    store = make(steps=("완료 후보",), due_norm="2026-11-13")
    store.change_status("A-001", "완료", "사람", approved_by_human=approval)
    before = _snapshot(store)
    _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    assert _snapshot(store) == before


def test_unknown_assignee_flags_keep_chk020(make):
    store = make(assignee="확인 필요", status="확인 필요", due_norm="2026-11-13")
    _apply(store, DONE_TEXT, "완료 후보")
    a = store.get("A-001")
    assert a["status"] == "확인 필요" and "기한경과" in a["flags"]


def test_reject_writes_no_flags(make):
    store = make(due_norm="2026-11-13")
    before = _snapshot(store)
    _apply(store, PROPOSAL, "진행중", runner=reject_runner, conf="중", flags=("기한변경제안",), proposed_due="월요일")
    assert _snapshot(store) == before


def test_invalid_result_writes_no_flags(make):
    store = make(due_norm="2026-11-13")
    before = _snapshot(store)
    r = _result("없는 발언", "진행중", conf="중", flags=("기한변경제안",), proposed_due="월요일")
    with pytest.raises(FollowupValidationError):
        apply_followup(store, r, _tr(PROPOSAL), pass_runner(), CUR_DATE)
    assert _snapshot(store) == before


def test_store_failure_with_flags_preserves_snapshot(make, monkeypatch):
    store = make(due_norm="2026-11-13")
    before = _snapshot(store)
    monkeypatch.setattr(ActionStore, "_write", lambda self, actions: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="월요일")
    monkeypatch.undo()
    assert _snapshot(store) == before


def test_status_change_and_flags_single_write(make, monkeypatch):
    store = make(due_norm="2026-11-13")
    writes = []
    orig = ActionStore._write
    monkeypatch.setattr(ActionStore, "_write", lambda self, actions: (writes.append(1), orig(self, actions))[1])
    _apply(store, PROPOSAL, "진행중", conf="중", flags=("기한변경제안",), proposed_due="월요일")
    assert len(writes) == 1
    a = store.get("A-001")
    assert a["status"] == "진행중" and {"기한경과", "기한변경제안"} <= set(a["flags"])
    assert a["history"][-1]["to_status"] == "진행중"


# T-09-3: 각 판단에 신규 회의 근거 원문·화자·타임스탬프 부착
def test_evidence_text_speaker_timestamp_attached_on_transition_and_reassessment(make):
    store = make()
    _apply(store, WORKING, "진행중", conf="중")
    _apply(store, WORKING, "진행중", conf="중")
    for h in store.get("A-001")["history"][1:]:
        e = h["evidence"][0]
        assert (e["meeting_id"], e["speaker"], e["timestamp"], e["text"]) == (CUR, "김대리", TS, WORKING)
