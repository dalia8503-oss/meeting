"""Phase 6 최소 vertical slice: Analyst(stub) → Reviewer(stub) → PASS면 Action, REJECT면 0건. 실제 LLM 없음."""
import copy
import json

import pytest

from meeting_action_agent.pipeline import extract_review_convert
from meeting_action_agent.reviewer import ReviewOutputError, review_candidate
from meeting_action_agent.store import ActionStore
from tests.test_vertical_slice import GOLDEN, META, TRANSCRIPT, _analysis


@pytest.fixture
def store(store_path):
    return ActionStore(store_path)


def _analyst(*cands):
    return lambda p: json.dumps(_analysis(list(cands)), ensure_ascii=False)


def _verdict(v, reasons=None, ref="00:02:10"):
    out = {"target": {"type": "action", "ref": ref}, "verdict": v, "reasons": reasons or [], "attempt": 0}
    return lambda p: json.dumps(out, ensure_ascii=False)


def _with(**kw):
    g = copy.deepcopy(GOLDEN)
    g["candidate"].update(kw)
    return g


def test_pass_creates_A001(store):
    r = extract_review_convert(TRANSCRIPT, _analyst(GOLDEN), _verdict("PASS"), store=store, metadata=META)
    assert [(o.kind, o.action_id) for o in r.conversion.outcomes] == [("created", "A-001")]
    assert r.rejected == () and r.reviews[0]["verdict"] == "PASS"
    assert store.get("A-001")["assignee"] == "김대리"


def test_semantic_reject_creates_no_action(store):
    reasons = [{"code": "지시아님", "detail": "제안일 뿐"}]
    r = extract_review_convert(TRANSCRIPT, _analyst(GOLDEN), _verdict("REJECT", reasons), store=store, metadata=META)
    assert r.conversion.outcomes == () and len(r.rejected) == 1
    assert store.load_all() == []


@pytest.mark.parametrize("kw,code", [
    ({"task": "예산 승인", "evidence": "예산 승인 요청"}, "인용불일치"),   # transcript에 없는 업무/근거
    ({"assignee": "박과장"}, "허위담당자"),
    ({"due_date": "다음 달 말까지"}, "허위기한"),
    ({"due_date_normalized": "2026-11-13"}, "허위환산날짜"),
])
def test_code_check_rejects_even_if_runner_says_pass(store, kw, code):
    r = extract_review_convert(TRANSCRIPT, _analyst(_with(**kw)), _verdict("PASS"), store=store, metadata=META)
    assert r.conversion.outcomes == () and store.load_all() == []
    assert code in [x["code"] for x in r.reviews[0]["reasons"]]


def test_reviewer_output_with_modified_candidate_is_refused(store):
    bad = {"target": {"type": "action", "ref": "00:02:10"}, "verdict": "PASS", "reasons": [], "attempt": 0,
           "corrected_candidate": {"assignee": "박과장"}}
    with pytest.raises(ReviewOutputError):
        extract_review_convert(TRANSCRIPT, _analyst(GOLDEN), lambda p: json.dumps(bad), store=store, metadata=META)
    assert store.load_all() == []


def test_reject_without_reason_and_wrong_target_refused():
    u = TRANSCRIPT[1]
    with pytest.raises(ReviewOutputError):
        review_candidate(GOLDEN["candidate"], u, _verdict("REJECT"))
    with pytest.raises(ReviewOutputError, match="대상"):
        review_candidate(GOLDEN["candidate"], u, _verdict("PASS", ref="00:01:00"))


def test_reviewer_does_not_mutate_candidate():
    c = copy.deepcopy(GOLDEN["candidate"])
    before = copy.deepcopy(c)
    review_candidate(c, TRANSCRIPT[1], _verdict("PASS"))
    assert c == before
