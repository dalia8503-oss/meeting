"""T-BRIEF-AGENT: Meeting Briefing Agent + Briefing Reviewer (mock/injected runner, LLM 호출 없음)."""
import copy
import json

import pytest

from meeting_action_agent.briefing_agent import (
    BriefingReviewerOutputError,
    code_check_briefing,
    generate_briefing,
    review_briefing,
)

APPROVAL = {"approver": "팀장", "approved_at": "2026-11-11T10:00:00+09:00"}


def _action(aid, status, assignee="김대리", task="도면 수정 및 공유", norm="2026-11-24", raw="다음 주 화요일까지",
            overdue=False):
    return {
        "action_id": aid, "assignee": assignee, "task": task, "due_date_raw": raw,
        "due_date_normalized": norm, "status": status, "overdue": overdue,
        "awaiting_human_confirmation": status == "완료 후보", "flags": [],
        "evidence": {"meeting_id": "20261110-01", "timestamp": "00:05:00", "text": "근거"},
    }


@pytest.fixture
def binput():
    return {
        "previous_meeting": {"meeting_id": "20261110-01", "title": "1차 회의", "meeting_date": "2026-11-10"},
        "current_date": "2026-11-12",
        "key_discussions": [{"text": "도면 수정 논의", "evidence_timestamps": ["00:01:00"]}],
        "decisions": {"include_in_briefing": False,
                      "items": [{"text": "A안으로 진행하기로 했다", "evidence_timestamps": ["00:02:00"]}]},
        "open_issues": [{"text": "품질 문제 추가 검토", "evidence_timestamps": ["00:03:00"]}],
        "active_actions": [
            _action("A-001", "완료 후보"),
            _action("A-002", "진행중", assignee="박대리", task="검토 의견 정리", norm="2026-11-24"),
            _action("A-003", "신규", assignee="이과장", task="자료 취합", norm=None, raw="이번 주 안으로"),
        ],
        "completed_actions": [],
        "needs_confirmation": [{"source": "analysis", "text": "담당자 불명확", "evidence_timestamps": ["00:04:00"]}],
    }


def good(**over):
    b = {
        "briefing_text": "지난 회의에서는 도면 수정이 논의되었습니다. 김대리의 도면 수정 및 공유 업무는 완료 후보로 최종 확인이 필요합니다. "
                         "박대리의 검토 의견 정리는 11월 24일까지 진행 중입니다. 품질 문제는 추가 검토 안건입니다.",
        "sections": {"previous_summary": "도면 수정 논의", "active_actions": "완료 후보, 진행 중",
                     "confirmation_items": "완료 후보 최종 확인, 담당자 불명확"},
        "referenced_action_ids": ["A-001", "A-002"],
    }
    b.update(over)
    return b


def gen_seq(*outputs, log=None):
    it = iter(outputs)

    def runner(prompt):
        if log is not None:
            log.append(prompt)
        out = next(it)
        return json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
    return runner


def rev_seq(*verdicts, log=None):
    it = iter(verdicts)

    def runner(prompt):
        if log is not None:
            log.append(prompt)
        v = next(it)
        if isinstance(v, str) and v in ("PASS", "REJECT"):
            v = {"verdict": v, "reasons": [] if v == "PASS" else [{"code": "사실생성", "detail": "입력에 없음"}]}
        return json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
    return runner


def text(t, **kw):
    return good(briefing_text=t, **kw)


def codes(binput, briefing):
    return {r["code"] for r in code_check_briefing(binput, briefing)}


# 1, 2, 4, 5, 9. 정상 생성 + 상태 보존 + 확인 필요 반영
def test_normal_briefing_generated(binput):
    r = generate_briefing(binput, gen_seq(good()), rev_seq("PASS"))
    assert r.approved and r.outcome == "approved"
    assert r.briefing["referenced_action_ids"] == ["A-001", "A-002"]
    assert "완료 후보" in r.briefing["briefing_text"] and "진행 중" in r.briefing["briefing_text"]
    assert r.briefing["sections"]["confirmation_items"]
    assert (r.generator_calls, r.reviewer_calls) == (1, 1)


def test_new_status_not_relabelled_in_progress(binput):
    binput["active_actions"] = [_action("A-003", "신규", assignee="이과장", norm=None, raw="이번 주 안으로")]
    b = good(briefing_text="이과장의 자료 취합은 이번 주 안으로 진행 중입니다.", referenced_action_ids=["A-003"])
    assert "상태왜곡" in codes(binput, b)  # 진행중 Action이 없는데 '진행 중'
    ok = good(briefing_text="이과장의 자료 취합은 새로 요청된 업무입니다.", referenced_action_ids=["A-003"],
              sections={"previous_summary": "", "active_actions": "신규", "confirmation_items": "담당자 불명확"})
    assert codes(binput, ok) == set()


# 3, J. 존재하지 않는 Action ID
def test_unknown_action_id_rejected(binput):
    assert "없는Action참조" in codes(binput, good(referenced_action_ids=["A-999"]))


# 6. 완료 후보를 완료로 표현
@pytest.mark.parametrize("phrase", ["도면 수정은 완료되었습니다.", "도면 수정은 완료했습니다.", "도면 수정은 끝났습니다."])
def test_done_candidate_as_completed_rejected(binput, phrase):
    assert "완료후보오표현" in codes(binput, text(f"지난 회의 도면 수정 논의. {phrase} 김대리 11월 24일."))


def test_candidate_phrases_allowed(binput):
    for ok in ["완료 후보입니다.", "완료 보고가 있어 최종 확인이 필요합니다.", "완료 여부를 최종 확인해야 합니다."]:
        assert codes(binput, text(f"김대리의 도면 수정은 {ok}")) == set()


# 7. Human-approved 완료 표현은 허용 (코드 통과 + Reviewer PASS)
def test_human_approved_completed_allowed(binput):
    binput["completed_actions"] = [{**_action("A-009", "완료", assignee="최대리", task="보고서 제출"),
                                    "approved_by_human": APPROVAL}]
    b = text("최대리의 보고서 제출은 승인되어 완료되었습니다. 박대리는 11월 24일까지 진행 중입니다.",
             referenced_action_ids=["A-009", "A-002"])
    assert codes(binput, b) == set()
    assert generate_briefing(binput, gen_seq(b), rev_seq("PASS")).approved


# 8. approval 없는 완료 → fail closed (completed_actions 비어 있으면 어떤 완료 단정도 거부)
def test_completion_without_approval_fail_closed(binput):
    bad = text("박대리의 검토 의견 정리는 완료되었습니다.", referenced_action_ids=["A-002"])
    assert codes(binput, bad) & {"미승인완료", "완료후보오표현"}
    r = generate_briefing(binput, gen_seq(bad, bad), rev_seq("PASS", "PASS"))
    assert not r.approved and r.briefing is None and r.reviewer_calls == 0  # 코드가 LLM PASS를 기다리지 않고 막음


# 10, 11, E. open issue를 결정으로 / decisions 사용
def test_open_issue_as_decision_rejected(binput):
    assert "미결을결정으로" in codes(binput, text("품질 문제는 결정되었습니다. 박대리 11월 24일."))


def test_decisions_content_blocked(binput):
    b = text("A안으로 진행하기로 했다는 내용입니다. 박대리 11월 24일까지 진행 중입니다.", referenced_action_ids=["A-002"])
    assert "decisions사용" in codes(binput, b)


# 12, 13. overdue
def test_overdue_true_expression_allowed(binput):
    binput["active_actions"][1]["overdue"] = True
    b = text("박대리의 검토 의견 정리는 11월 24일 기한이 지났습니다.", referenced_action_ids=["A-002"])
    assert "기한초과오표현" not in codes(binput, b)


def test_overdue_false_expression_rejected(binput):
    b = text("박대리의 검토 의견 정리는 기한이 지났습니다. 11월 24일.", referenced_action_ids=["A-002"])
    assert "기한초과오표현" in codes(binput, b)


# 14, 15, 16. hallucination
def test_assignee_hallucination_rejected(binput):
    b = text("정과장의 자료 취합이 남아 있습니다. 완료 후보 확인 필요.", referenced_action_ids=["A-003"])
    assert "담당자오류" in codes(binput, b)


def test_due_date_hallucination_rejected(binput):
    b = text("박대리의 검토 의견 정리는 11월 30일까지 진행 중입니다.", referenced_action_ids=["A-002"])
    assert "기한오류" in codes(binput, b)


def test_fact_not_in_input_via_llm_reviewer_reject(binput):
    # 코드로 못 잡는 의미적 사실 생성은 LLM Reviewer가 REJECT
    r = generate_briefing(binput, gen_seq(good(), good()), rev_seq("REJECT", "REJECT"))
    assert not r.approved and r.outcome == "rejected" and r.reasons[0]["code"] == "사실생성"


# 구조/길이
def test_structure_and_length_violations(binput):
    assert "구조오류" in codes(binput, good(briefing_text=""))
    assert "구조오류" in codes(binput, good(briefing_text="가" * 700))
    assert "구조오류" in codes(binput, {**good(), "extra": 1})
    assert "구조오류" in codes(binput, good(sections={"previous_summary": ""}))
    assert "구조오류" in codes(binput, good(referenced_action_ids=[]))  # active_actions가 있는데 언급 없음


# 17, 18. Reviewer PASS / REJECT
def test_reviewer_pass_and_reject(binput):
    assert review_briefing(binput, good(), rev_seq("PASS"))["verdict"] == "PASS"
    v = review_briefing(binput, good(), rev_seq("REJECT"))
    assert v["verdict"] == "REJECT" and v["reasons"][0]["code"] == "사실생성"


def test_code_reject_not_overturned_by_llm_pass(binput):
    log = []
    v = review_briefing(binput, good(referenced_action_ids=["A-999"]), rev_seq("PASS", log=log))
    assert v["verdict"] == "REJECT" and log == []


# 19, 20. retry 최대 1회, second PASS
def test_retry_once_then_pass_with_feedback(binput):
    glog, rlog = [], []
    r = generate_briefing(binput, gen_seq(good(), good(), log=glog), rev_seq("REJECT", "PASS", log=rlog))
    assert r.approved and (r.generator_calls, r.reviewer_calls) == (2, 2)
    assert "재생성 참고정보" in glog[1] and "입력에 없음" in glog[1] and "재생성 참고정보" not in glog[0]


# 19, 21. 최대 1회 재시도 후 fail closed
def test_second_reject_fail_closed_no_third_attempt(binput):
    r = generate_briefing(binput, gen_seq(good(), good()), rev_seq("REJECT", "REJECT"))
    assert not r.approved and r.briefing is None and r.rejected_draft is not None
    assert (r.generator_calls, r.reviewer_calls) == (2, 2)


def test_invalid_generator_output_gets_one_retry(binput):
    r = generate_briefing(binput, gen_seq("not json", good()), rev_seq("PASS"))
    assert r.approved and r.generator_calls == 2
    r2 = generate_briefing(binput, gen_seq("not json", "still not json"), rev_seq())
    assert not r2.approved and r2.generator_calls == 2


# 22. invalid Reviewer output → fail closed
@pytest.mark.parametrize("bad", [
    "not json", '{"verdict": "MAYBE", "reasons": []}', '{"verdict": "REJECT", "reasons": []}',
    '{"verdict": "PASS", "reasons": [], "revised_text": "고친 문장"}',
    '{"verdict": "REJECT", "reasons": [{"code": "임의코드", "detail": "x"}]}',
])
def test_invalid_reviewer_output_fail_closed(binput, bad):
    with pytest.raises(BriefingReviewerOutputError):
        review_briefing(binput, good(), rev_seq(bad))
    r = generate_briefing(binput, gen_seq(good(), good()), rev_seq(bad))
    assert not r.approved and r.outcome == "reviewer_invalid" and r.briefing is None
    assert r.generator_calls == 1  # 판정 불능은 재생성하지 않는다


# 23. 원본 BriefingInput 불변
def test_briefing_input_unchanged(binput):
    before = copy.deepcopy(binput)
    generate_briefing(binput, gen_seq(good(), good()), rev_seq("REJECT", "PASS"))
    assert binput == before


# 생성 prompt는 BriefingInput만 사용한다
def test_prompt_contains_only_briefing_input_facts(binput):
    glog = []
    generate_briefing(binput, gen_seq(good(), log=glog), rev_seq("PASS"))
    assert "도면 수정 및 공유" in glog[0] and "include_in_briefing" in glog[0]
