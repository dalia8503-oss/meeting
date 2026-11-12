"""T-SUMMARY-FIX-1: Meeting Analyst 요약 분류 품질 (key_discussions / decisions / needs_confirmation).

실제 LLM 호출 없음 — 주입 runner(고정 응답)로 prompt 규칙과 분류 검증만 고정한다.
Action 추출·Follow-up·Human Review 정책은 다루지 않는다 (기존 테스트가 담당).
"""
import copy

import pytest

from meeting_action_agent.analyst import AnalysisValidationError, analyze_transcript, build_prompt

MID = "20261110-02"
ORDER = "김대리 금요일까지 도면 수정해서 공유해 주세요."
DONE = "수정 완료해서 어제 공유했습니다."
DECIDE = "그럼 이번에는 A안으로 진행하겠습니다."
PROPOSAL = "다음에는 품질 문제도 한번 보면 좋겠습니다."


def _t(*rows):
    return [{"meeting_id": MID, "speaker": s, "timestamp": ts, "text": x} for s, ts, x in rows]


def _a(**over):
    a = {"meeting_id": MID, "key_discussions": [], "decisions": [], "open_issues": [],
         "needs_confirmation": [], "action_candidates": []}
    a.update(over)
    return a


def _item(*ts, text="x"):
    return {"text": text, "evidence_timestamps": list(ts)}


def _order_candidate(ts):
    return {"instruction_type": "지시·요청", "candidate": {
        "meeting_id": MID, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정해서 공유",
        "due_date": "금요일까지", "due_date_normalized": None, "evidence": ORDER, "timestamp": ts,
        "status": "신규", "flags": []}}


def _run(analysis, transcript, speaker_map=None):
    return analyze_transcript(transcript, lambda p: copy.deepcopy(analysis), speaker_map)


# ---- A. 명확한 지시는 key_discussions에도 남는다 ----
def test_clear_order_missing_from_key_discussions_is_backfilled_from_candidate_only():
    t = _t(("팀장", "00:01:00", ORDER))
    out = _run(_a(action_candidates=[_order_candidate("00:01:00")]), t)
    kd = out["key_discussions"]
    assert len(kd) == 1 and kd[0]["evidence_timestamps"] == ["00:01:00"]
    assert "김대리" in kd[0]["text"] and "금요일까지" in kd[0]["text"] and "도면 수정해서 공유" in kd[0]["text"]
    assert out["action_candidates"][0]["candidate"]["status"] == "신규"  # 후보·상태 불변


def test_backfill_skips_when_covered_or_not_clear_order():
    t = _t(("팀장", "00:01:00", ORDER))
    cand = _order_candidate("00:01:00")
    amb = copy.deepcopy(cand)
    amb["instruction_type"] = "지시여부애매"
    amb["candidate"].update(status="확인 필요", flags=["지시여부애매"], flag_notes={"지시여부애매": {"note": "애매"}})
    assert _run(_a(action_candidates=[amb]), t)["key_discussions"] == []
    covered = _a(key_discussions=[_item("00:01:00", text="도면 수정 요청")], action_candidates=[cand])
    assert len(_run(covered, t)["key_discussions"]) == 1


def test_clear_order_reflected_in_key_discussions_passes():
    t = _t(("팀장", "00:01:00", ORDER))
    a = _a(key_discussions=[_item("00:01:00", text="김대리에게 금요일까지 도면 수정 및 공유 요청")],
           action_candidates=[_order_candidate("00:01:00")])
    assert _run(a, t)["action_candidates"]  # ActionCandidate 정책 그대로 유지


# ---- B. 완료 보고 ≠ decision ----
def test_completion_report_as_decision_rejected():
    t = _t(("팀장", "00:01:00", DONE))
    with pytest.raises(AnalysisValidationError, match="decisions"):
        _run(_a(decisions=[_item("00:01:00", text="도면 수정 완료")]), t)


def test_completion_report_as_key_discussion_allowed():
    t = _t(("팀장", "00:01:00", DONE))
    _run(_a(key_discussions=[_item("00:01:00", text="도면 수정 완료 보고가 있었음")]), t)


def test_real_decision_kept():
    t = _t(("팀장", "00:01:00", DECIDE))
    assert _run(_a(decisions=[_item("00:01:00", text="A안으로 진행")]), t)["decisions"]


# ---- C. 화자 확인이 필요한 완료 보고 ----
def test_unconfirmed_speaker_completion_report_needs_confirmation():
    t = _t(("speaker_2", "00:01:00", DONE))
    with pytest.raises(AnalysisValidationError, match="needs_confirmation"):
        _run(_a(key_discussions=[_item("00:01:00", text="도면 수정 완료 보고")]), t)  # speaker_map 없음
    ok = _a(key_discussions=[_item("00:01:00", text="도면 수정 완료 보고")],
            needs_confirmation=[_item("00:01:00", text="도면 수정 완료 보고가 있으나 발언자 확인 필요")])
    _run(ok, t)


def test_confirmed_speaker_completion_report_not_decision_and_no_forced_confirmation():
    t = _t(("speaker_2", "00:01:00", DONE))
    sm = {"speaker_2": {"name": "김대리"}}
    _run(_a(key_discussions=[_item("00:01:00", text="김대리 도면 수정 완료 보고")]), t, sm)
    with pytest.raises(AnalysisValidationError, match="decisions"):  # 화자 확인 ≠ 회의 결정
        _run(_a(decisions=[_item("00:01:00", text="김대리 완료")]), t, sm)


# ---- 제안은 기존 정책 유지 (CHK021) ----
def test_proposal_stays_open_issue_not_decision():
    t = _t(("팀장", "00:01:00", PROPOSAL))
    _run(_a(open_issues=[_item("00:01:00", text="품질 문제 검토 제안")]), t)
    with pytest.raises(AnalysisValidationError, match="decisions"):
        _run(_a(decisions=[_item("00:01:00", text="품질 문제 검토")]), t)


# ---- prompt 규칙 ----
def test_prompt_states_summary_rules():
    p = build_prompt(_t(("speaker_2", "00:01:00", DONE)))
    assert "key_discussions" in p and "ActionCandidate" in p
    assert "완료 보고" in p and "decisions에 넣지 않는다" in p
    assert "needs_confirmation" in p and "담당자인지 확인" in p
