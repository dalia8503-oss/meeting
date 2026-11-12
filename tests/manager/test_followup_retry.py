"""T-09-7 Follow-up → Reviewer → (REJECT 시 1회 재분석) → PASS일 때만 apply. fake runner로 검증한다."""
import json

import pytest

from meeting_action_agent.manager.followup_apply import (
    apply_followup,
    apply_followup_with_retry,
    apply_followups_with_retry,
)
from meeting_action_agent.store import ActionStore

from .test_followup_apply import (
    CUR,
    CUR_DATE,
    DONE_TEXT,
    NOT_STARTED,
    WORKING,
    _action,
    _result,
    _snapshot,
    _tr,
)


def _input(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("## INPUT\n", 1)[1])[0]


def _ref(prompt):
    return _input(prompt)["followup_result"]["action_id"]


def reviewer_seq(*verdicts, log=None):
    """호출 순서대로 PASS/REJECT를 내는 fake Reviewer. 호출마다 prompt를 log에 남긴다."""
    it = iter(verdicts)

    def runner(prompt):
        if log is not None:
            log.append(prompt)
        v = next(it)
        reasons = [] if v == "PASS" else [{"code": "판정비도출", "detail": "완료 표현임"}]
        return json.dumps({"target": {"type": "followup", "ref": _ref(prompt)}, "verdict": v,
                           "reasons": reasons, "attempt": 0})
    return runner


def followup_seq(*results, log=None):
    """재분석 runner: 호출될 때마다 준비된 결과를 하나씩 낸다. 더 부르면 StopIteration으로 실패한다."""
    it = iter(results)

    def runner(prompt):
        if log is not None:
            log.append(prompt)
        return next(it)
    return runner


@pytest.fixture
def make(store_path, new_action):
    store = ActionStore(store_path)

    def _make(*ids, **kw):
        for aid in ids or ("A-001",):
            store.create(_action(new_action, aid, **kw))
        return store
    return _make


def run(store, first, fu_runner, rv_runner, text=DONE_TEXT):
    return apply_followup_with_retry(store, first, _tr(text), fu_runner, rv_runner, CUR_DATE)


# 1, 2. 1차 PASS: 즉시 apply, retry 없음 (Case C)
def test_first_pass_applies_without_retry(make):
    store = make()
    fu, rv = [], []
    out = run(store, _result(DONE_TEXT, "완료 후보"), followup_seq(log=fu), reviewer_seq("PASS", log=rv))
    assert out.outcome == "applied" and store.get("A-001")["status"] == "완료 후보"
    assert len(fu) == 0 and len(rv) == 1
    assert (out.followup_calls, out.review_calls) == (1, 1)


# 3. REJECT → PASS (Case A)
def test_reject_then_pass_applies_second_result(make):
    store = make()
    fu, rv = [], []
    first = _result(DONE_TEXT, "진행중", conf="중")
    second = json.dumps(_result(DONE_TEXT, "완료 후보"))
    out = run(store, first, followup_seq(second, log=fu), reviewer_seq("REJECT", "PASS", log=rv))
    a = store.get("A-001")
    assert out.outcome == "applied" and a["status"] == "완료 후보"
    assert a["history"][-1]["evidence"][0]["text"] == DONE_TEXT
    assert (out.followup_calls, out.review_calls) == (2, 2)
    assert len(a["history"]) == 2  # 생성 + 최종 PASS 1건 (1차 REJECT 결과는 기록되지 않음)


# 4, 18. REJECT → REJECT: snapshot 완전 동일 (Case B)
def test_reject_reject_leaves_snapshot_identical(make):
    store = make(due_norm="2026-11-13")  # overdue여도 flag 미저장
    before = _snapshot(store)
    second = json.dumps(_result(DONE_TEXT, "완료 후보"))
    out = run(store, _result(DONE_TEXT, "완료 후보", flags=("기한변경제안",), proposed_due="다음주"),
              followup_seq(second), reviewer_seq("REJECT", "REJECT"))
    assert out.outcome == "rejected" and out.review["verdict"] == "REJECT"
    assert _snapshot(store) == before
    a = store.get("A-001")
    assert a["flags"] == [] and a["status"] == "신규"


# 5, 6, 11. REJECT → 2차 invalid: Reviewer #2·3차 분석 없음, write 없음
@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"meeting_id": CUR}),                                  # 스키마 위반
    json.dumps(_result("없는 발언", "완료 후보")),                     # Evidence 비실재
    json.dumps(_result(DONE_TEXT, "완료")),                            # Agent 완료 금지
    json.dumps(_result(DONE_TEXT, "완료 후보", action_id="A-999")),   # action_id 불일치
])
def test_second_result_invalid_stops_without_reviewer2(make, bad):
    store = make()
    before = _snapshot(store)
    fu, rv = [], []
    out = run(store, _result(DONE_TEXT, "완료 후보"), followup_seq(bad, log=fu), reviewer_seq("REJECT", log=rv))
    assert out.outcome == "rejected" and _snapshot(store) == before
    assert len(fu) == 1 and len(rv) == 1
    assert (out.followup_calls, out.review_calls) == (2, 1)


def test_second_result_wrong_meeting_id_is_invalid(make):
    store = make()
    second = _result(DONE_TEXT, "완료 후보")
    second["meeting_id"] = "20990101-01"
    rv = []
    out = run(store, _result(DONE_TEXT, "완료 후보"), followup_seq(json.dumps(second)), reviewer_seq("REJECT", log=rv))
    assert out.outcome == "rejected" and len(rv) == 1 and store.get("A-001")["status"] == "신규"


# 6, 7. 상한: 분석 최대 2회(첫 결과 + 재분석 1), Reviewer 최대 2회
def test_call_limits_even_if_runner_would_keep_going(make):
    store = make()
    fu, rv = [], []
    second = json.dumps(_result(DONE_TEXT, "완료 후보"))
    out = run(store, _result(DONE_TEXT, "완료 후보"), followup_seq(second, second, second, log=fu),
              reviewer_seq("REJECT", "REJECT", "PASS", "PASS", log=rv))
    assert len(fu) == 1 and len(rv) == 2 and out.outcome == "rejected"


# 8. Reviewer reason·이전 결과·Transcript·Previous Action이 재분석 runner에 전달된다
def test_reviewer_feedback_passed_to_second_runner(make):
    store = make()
    fu = []
    second = json.dumps(_result(DONE_TEXT, "완료 후보"))
    run(store, _result(DONE_TEXT, "진행중", conf="중"), followup_seq(second, log=fu), reviewer_seq("REJECT", "PASS"))
    p = fu[0]
    assert "완료 표현임" in p and "판정비도출" in p
    assert DONE_TEXT in p                                    # Current Transcript
    assert "도면 수정해서 공유" in p                          # Previous Action
    assert '"proposed_status": "진행중"' in p                 # 1차 FollowupResult
    assert "근거로 사용하지" in p                             # 참고정보일 뿐이라는 경고


# 9. feedback 문구는 Evidence가 될 수 없다
def test_feedback_text_cannot_be_evidence(make):
    store = make()
    before = _snapshot(store)
    rv = []
    forged = _result("완료 표현임", "완료 후보")
    out = run(store, _result(DONE_TEXT, "진행중", conf="중"), followup_seq(json.dumps(forged)),
              reviewer_seq("REJECT", log=rv))
    assert out.outcome == "rejected" and len(rv) == 1 and _snapshot(store) == before


# 10. 2차 Evidence도 Transcript 원문과 정확히 일치해야 한다
def test_second_evidence_must_match_transcript_exactly(make):
    store = make()
    before = _snapshot(store)
    r2 = _result(DONE_TEXT, "완료 후보")
    r2["evidence"][0]["text"] = "도면 수정해서 공유했습니다"  # 마침표 누락
    out = run(store, _result(DONE_TEXT, "진행중", conf="중"), followup_seq(json.dumps(r2)), reviewer_seq("REJECT"))
    assert out.outcome == "rejected" and _snapshot(store) == before


def test_second_result_completion_safety_guard_applies(make):
    store = make()
    before = _snapshot(store)
    out = run(store, _result(NOT_STARTED, "미완료"), followup_seq(json.dumps(_result(NOT_STARTED, "완료 후보"))),
              reviewer_seq("REJECT"), text=NOT_STARTED)
    assert out.outcome == "rejected" and _snapshot(store) == before


# 12. 1차 REJECT flag 미저장 / 최종 PASS flag만 저장
def test_only_final_pass_flags_are_saved(make):
    store = make(due_norm="2026-11-13")
    text = "금요일까지는 어렵고 다음주 월요일까지 하겠습니다."
    first = _result(text, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    second = _result(text, "진행중", conf="중")  # 최종 결과에는 기한변경제안 없음
    out = run(store, first, followup_seq(json.dumps(second)), reviewer_seq("REJECT", "PASS"), text=text)
    a = store.get("A-001")
    assert out.outcome == "applied"
    assert "기한변경제안" not in a["flags"] and "기한변경제안" not in a.get("flag_notes", {})
    assert "기한경과" in a["flags"]


# 5. 최종 PASS 이전에는 어떤 write도 없다
def test_no_write_before_final_pass(make):
    store = make()
    seen = []

    def fu_runner(prompt):
        seen.append(("fu", store.get("A-001")["status"], len(store.get("A-001")["history"])))
        return json.dumps(_result(DONE_TEXT, "완료 후보"))

    verdicts = iter(["REJECT", "PASS"])

    def rv(prompt):
        seen.append(("rv", store.get("A-001")["status"], len(store.get("A-001")["history"])))
        return reviewer_seq(next(verdicts))(prompt)

    run(store, _result(DONE_TEXT, "진행중", conf="중"), fu_runner, rv)
    assert seen == [("rv", "신규", 1), ("fu", "신규", 1), ("rv", "신규", 1)]
    assert store.get("A-001")["status"] == "완료 후보"


# 13. due immutable
def test_due_date_immutable_through_retry(make):
    store = make(due_norm="2026-11-13")
    text = "금요일까지는 어렵고 다음주 월요일까지 하겠습니다."
    second = _result(text, "진행중", conf="중", flags=("기한변경제안",), proposed_due="다음주 월요일")
    run(store, _result(text, "진행중", conf="중"), followup_seq(json.dumps(second)),
        reviewer_seq("REJECT", "PASS"), text=text)
    a = store.get("A-001")
    assert (a["due_date"], a["due_date_normalized"]) == ("금요일", "2026-11-13")
    assert "기한변경제안" in a["flags"]


# 14. CHK020 유지
def test_chk020_preserved_after_retry(make):
    store = make(assignee="확인 필요", status="확인 필요")
    out = run(store, _result(DONE_TEXT, "진행중", conf="중"),
              followup_seq(json.dumps(_result(DONE_TEXT, "완료 후보"))), reviewer_seq("REJECT", "PASS"))
    a = store.get("A-001")
    assert a["status"] == "확인 필요" and out.outcome == "reassessed" and "CHK020" in a["history"][-1]["note"]


# 15. 완료 후보 lock
def test_done_candidate_lock_preserved_after_retry(make):
    store = make()
    store.change_status("A-001", "완료 후보", "Agent")
    out = run(store, _result(NOT_STARTED, "진행중", conf="중"),
              followup_seq(json.dumps(_result(NOT_STARTED, "미완료"))), reviewer_seq("REJECT", "PASS"),
              text=NOT_STARTED)
    a = store.get("A-001")
    assert a["status"] == "완료 후보" and out.outcome == "reassessed" and a["history"][-1]["kind"] == "재판정"


# 16. 완료 Action 보호: runner·Reviewer 모두 호출하지 않는다
def test_done_action_protected(make, approval):
    store = make()
    store.change_status("A-001", "완료 후보", "Agent")
    store.change_status("A-001", "완료", "사람", approved_by_human=approval)
    before = _snapshot(store)
    fu, rv = [], []
    out = run(store, _result(NOT_STARTED, "미완료"), followup_seq(log=fu), reviewer_seq(log=rv), text=NOT_STARTED)
    assert out.outcome == "skipped" and not fu and not rv and _snapshot(store) == before


# 1차 결과가 invalid면 Reviewer·재분석 없이 종료
def test_first_result_invalid_no_retry(make):
    store = make()
    before = _snapshot(store)
    fu, rv = [], []
    from meeting_action_agent.manager.followup import FollowupValidationError
    with pytest.raises(FollowupValidationError):
        run(store, _result("없는 발언", "완료 후보"), followup_seq(log=fu), reviewer_seq(log=rv))
    assert not fu and not rv and _snapshot(store) == before


# 17. 여러 Action 독립 처리
def test_multiple_actions_retry_independently(make):
    store = make("A-001", "A-002", "A-003")
    before3 = json.dumps(store.get("A-003"), sort_keys=True)
    firsts = [_result(DONE_TEXT, "완료 후보", action_id=i) for i in ("A-001", "A-002", "A-003")]
    seconds = {i: json.dumps(_result(DONE_TEXT, "완료 후보", action_id=i)) for i in ("A-002", "A-003")}
    fu_calls = []

    def fu(prompt):
        aid = _input(prompt)["action"]["action_id"]
        fu_calls.append(aid)
        return seconds[aid]

    plan = {"A-001": iter(["PASS"]), "A-002": iter(["REJECT", "PASS"]), "A-003": iter(["REJECT", "REJECT"])}

    def rv(prompt):
        aid = _ref(prompt)
        v = next(plan[aid])
        return json.dumps({"target": {"type": "followup", "ref": aid}, "verdict": v,
                           "reasons": [] if v == "PASS" else [{"code": "판정비도출", "detail": "x"}], "attempt": 0})

    outs = apply_followups_with_retry(store, firsts, _tr(DONE_TEXT), fu, rv, CUR_DATE)
    assert [o.outcome for o in outs] == ["applied", "applied", "rejected"]
    assert fu_calls == ["A-002", "A-003"]
    assert store.get("A-001")["status"] == "완료 후보" and store.get("A-002")["status"] == "완료 후보"
    assert json.dumps(store.get("A-003"), sort_keys=True) == before3


# 19. 정책 7: 미언급 + 기한경과 → write 없음
def test_no_mention_overdue_writes_nothing(make):
    store = make(due_norm="2026-11-13")
    before = _snapshot(store)
    res = _result("", "확인 필요", conf="하", evidence=False, flags=("기한경과",))
    out = apply_followup(store, res, _tr("무관한 발언"), reviewer_seq("PASS"), CUR_DATE)
    assert out.outcome == "skipped" and _snapshot(store) == before
    a = store.get("A-001")
    assert a["flags"] == [] and not a.get("flag_notes") and len(a["history"]) == 1


def test_no_mention_overdue_via_retry_writes_nothing(make):
    store = make(due_norm="2026-11-13")
    before = _snapshot(store)
    out = run(store, _result("", "확인 필요", conf="하", evidence=False), followup_seq(), reviewer_seq("PASS"),
              text="무관한 발언")
    assert out.outcome == "skipped" and _snapshot(store) == before


# 20. history continuity
def test_history_continuity_after_retry(make):
    store = make()
    run(store, _result(DONE_TEXT, "진행중", conf="중"), followup_seq(json.dumps(_result(DONE_TEXT, "완료 후보"))),
        reviewer_seq("REJECT", "PASS"))
    ActionStore(store.path).load_all()  # history 연속성 검증
    run(store, _result(NOT_STARTED, "미완료"), followup_seq(), reviewer_seq("PASS"), text=NOT_STARTED)
    h = store.get("A-001")["history"]
    assert len(h) == 3 and h[-1]["kind"] == "재판정"
    ActionStore(store.path).load_all()


# T-09-4: 확신도 하 / low_confidence만 근거 / 상충 / 철회 → 확인 필요 (근거 첨부)
@pytest.mark.parametrize("text,flags", [
    ("도면 수정은 한 것 같기도 한데요.", ()),  # 확신도 하 / low_confidence만 근거
    ("도면 수정은 공유했다고 했는데 박과장은 아직이라고 합니다.", ()),  # 상충
    ("도면 수정 공유한 건 취소하고 다시 하겠습니다.", ()),              # 철회
])
def test_t0904_uncertain_evidence_becomes_needs_check_with_evidence(make, text, flags):
    store = make()
    out = apply_followup(store, _result(text, "확인 필요", conf="하", flags=flags), _tr(text),
                         reviewer_seq("PASS"), CUR_DATE)
    a = store.get("A-001")
    assert out.outcome == "applied" and a["status"] == "확인 필요"
    assert a["history"][-1]["evidence"][0]["text"] == text


def test_t0904_low_confidence_cannot_be_done_candidate(make):
    from meeting_action_agent.contracts import SchemaValidationError
    store = make()
    before = _snapshot(store)
    with pytest.raises(SchemaValidationError):
        apply_followup(store, _result(DONE_TEXT, "완료 후보", conf="하"), _tr(DONE_TEXT),
                       reviewer_seq("PASS"), CUR_DATE)
    assert _snapshot(store) == before
