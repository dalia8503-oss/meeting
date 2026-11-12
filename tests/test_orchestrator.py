"""T-11 Multi-Agent Orchestrator. fake deterministic runner만 사용한다 (실제 LLM 호출 없음).

기존 test_followup_pipeline의 synthetic scenario(A-001 완료 후보 / A-002 신규 / 제안 제외)를 재사용한다.
"""
import json
import threading

import pytest

from meeting_action_agent.analyst import AnalysisValidationError
from meeting_action_agent.orchestrator import OrchestrationResult, run_orchestrated_meeting
from meeting_action_agent.pipeline import MeetingPipelineResult, run_meeting_pipeline
from meeting_action_agent.review import review_action
from meeting_action_agent.state import CompletionGuardError
from meeting_action_agent.store import ActionStore

from tests.test_followup_pipeline import (
    A1_DONE, CUR, DONE_TEXT, META, NEW_TEXT, PROPOSAL, QUESTION, TRANSCRIPT, TS_DONE, TS_NEW, TS_PROP, TS_QUES,
    _analysis, _candidate, _followup, _prev_action, analyst, candidate_reviewer, followup_reviewer, followup_runner,
)


@pytest.fixture
def store(store_path, new_action):
    s = ActionStore(store_path)
    s.create(_prev_action(new_action))
    return s


def orch(store, *, fu=None, fv=None, cands=None, cv="PASS", analyst_runner=None, fu_runner=None,
         fv_runner=None, cv_runner=None, transcript=TRANSCRIPT, meetings_dir=None):
    fu = fu if fu is not None else {"A-001": A1_DONE}
    fv = fv if fv is not None else {"A-001": "PASS"}
    cands = [_candidate()] if cands is None else cands
    return run_orchestrated_meeting(
        transcript,
        analyst_runner=analyst_runner or analyst(cands),
        candidate_reviewer_runner=cv_runner or candidate_reviewer(cv),
        followup_runner=fu_runner or followup_runner(fu),
        followup_reviewer_runner=fv_runner or followup_reviewer(fv),
        store=store, metadata=META, meetings_dir=meetings_dir)


# 1. Previous Action 없음 → 기존 1차 흐름 (순차)
def test_first_meeting_is_sequential(store_path):
    s = ActionStore(store_path)
    calls = []

    def fu(prompt):
        calls.append("followup")
        raise AssertionError("1차 회의에서는 Follow-up Agent를 실행하지 않는다")

    r = orch(s, fu_runner=fu)
    assert isinstance(r, OrchestrationResult) and isinstance(r, MeetingPipelineResult)
    assert r.mode == "sequential" and calls == []
    assert [e.agent for e in r.execution] == ["meeting-analyst"]
    assert [a["action_id"] for a in s.load_all()] == ["A-001"]
    assert s.get("A-001")["assignee"] == "박대리"


# 2. Previous Action 있음 → 두 Branch 실행
def test_second_meeting_runs_two_branches(store):
    r = orch(store)
    assert r.mode == "parallel"
    assert {e.agent for e in r.execution} == {"meeting-analyst", "followup-agent"}
    assert all(e.status == "ok" and e.finished_at >= e.started_at for e in r.execution)


# 3. 독립 실행 확인 — Barrier: 두 Branch가 동시에 대기해야만 통과한다 (순차면 timeout)
def test_branches_actually_overlap(store):
    barrier = threading.Barrier(2, timeout=5)
    threads = {}

    def a_runner(prompt):
        threads["a"] = threading.get_ident()
        barrier.wait()
        return analyst([_candidate()])(prompt)

    inner_fu = followup_runner({"A-001": A1_DONE})

    def f_runner(prompt):
        threads["f"] = threading.get_ident()
        barrier.wait()
        return inner_fu(prompt)

    r = orch(store, analyst_runner=a_runner, fu_runner=f_runner)
    assert threads["a"] != threads["f"]
    by = {e.agent: e for e in r.execution}
    a, f = by["meeting-analyst"], by["followup-agent"]
    assert a.started_at < f.finished_at and f.started_at < a.finished_at  # 구간 겹침
    assert r.overlapped is True
    assert store.get("A-001")["status"] == "완료 후보" and store.get("A-002")


# 4~8. merge / A-001 완료 후보 / A-002 신규 / proposal 제외 / Evidence 분리
def test_merge_scenario(store):
    r = orch(store)
    a1, a2 = store.get("A-001"), store.get("A-002")
    assert a1["status"] == "완료 후보" and a1["meeting_id"] != CUR
    assert (a2["assignee"], a2["task"], a2["due_date"], a2["status"]) == (
        "박대리", "검토 의견 정리해서 공유", "다음주 화요일까지", "신규")
    assert a2["due_date_normalized"] is not None or a2["due_date"] == "다음주 화요일까지"
    assert [a["action_id"] for a in store.load_all()] == ["A-001", "A-002"]
    assert [o.action_id for o in r.followup_outcomes] == ["A-001"]
    assert r.new_actions is not None and r.new_actions_error is None
    # proposal·질문은 Action이 되지 않는다
    assert all(PROPOSAL not in a["task"] and QUESTION not in a["task"] for a in store.load_all())
    # Evidence 분리: A-001은 완료 발언, A-002는 지시 발언
    assert DONE_TEXT in json.dumps(a1["history"], ensure_ascii=False)
    assert NEW_TEXT not in json.dumps(a1, ensure_ascii=False)
    assert DONE_TEXT not in json.dumps(a2, ensure_ascii=False)


def test_proposal_rejected_by_reviewer_makes_no_action(store):
    bad = _candidate(ts=TS_PROP, text=PROPOSAL, assignee="팀장", task="품질 문제 보기")
    r = orch(store, cands=[_candidate(), bad], cv_runner=_reject_ts(TS_PROP))
    assert [a["action_id"] for a in store.load_all()] == ["A-001", "A-002"]
    assert len(r.new_actions.rejected) == 1


def _reject_ts(bad_ts):
    base = candidate_reviewer("PASS")
    rej = candidate_reviewer("REJECT")
    return lambda prompt: (rej if f'"timestamp": "{bad_ts}"' in prompt else base)(prompt)


# 9. Analyst failure isolation — Follow-up 결과는 유지
def test_analyst_failure_isolated(store):
    def broken(prompt):
        return json.dumps({"meeting_id": CUR})  # 계약 위반

    r = orch(store, analyst_runner=broken)
    assert r.new_actions is None and r.new_actions_error
    assert store.get("A-001")["status"] == "완료 후보"
    assert [a["action_id"] for a in store.load_all()] == ["A-001"]
    by = {e.agent: e for e in r.execution}
    assert by["meeting-analyst"].status == "error" and by["followup-agent"].status == "ok"


# 10. Follow-up expected failure isolation — 신규 Action은 유지
def test_followup_invalid_output_isolated(store):
    r = orch(store, fu_runner=lambda prompt: "not json")
    assert [x["action_id"] for x in r.followup_invalid] == ["A-001"]
    assert store.get("A-001")["status"] == "신규"
    assert store.get("A-002")["status"] == "신규"
    assert r.new_actions_error is None


# 11. unexpected RuntimeError는 삼키지 않는다 (양쪽 Branch 모두) — write 없음
@pytest.mark.parametrize("which", ["analyst", "followup"])
def test_unexpected_runtime_error_propagates(store, which):
    def boom(prompt):
        raise RuntimeError("boom")

    kw = {"analyst_runner": boom} if which == "analyst" else {"fu_runner": boom}
    with pytest.raises(RuntimeError, match="boom"):
        orch(store, **kw)
    assert [a["action_id"] for a in store.load_all()] == ["A-001"]
    assert store.get("A-001")["status"] == "신규"


# 안전 위반(Agent '완료' 제안)은 전파되고 어떤 write도 없다 (fail-closed)
def test_done_proposal_is_fail_closed(store):
    with pytest.raises(CompletionGuardError):
        orch(store, fu={"A-001": _followup("A-001", TS_DONE, "김대리", DONE_TEXT, status="완료")})
    assert [a["action_id"] for a in store.load_all()] == ["A-001"]
    assert store.get("A-001")["status"] == "신규"


# 12. Reviewer REJECT 결과 미반영
def test_reviewer_reject_not_applied(store):
    r = orch(store, fv={"A-001": ["REJECT", "REJECT"]}, fu={"A-001": [A1_DONE, A1_DONE]}, cv="REJECT")
    assert store.get("A-001")["status"] == "신규"
    assert [a["action_id"] for a in store.load_all()] == ["A-001"]
    assert [o.outcome for o in r.followup_outcomes] == ["rejected"]
    assert r.new_actions.rejected


# 13. Follow-up retry regression (REJECT → 재분석 1회 → PASS)
def test_followup_retry_reused(store):
    log = []
    r = orch(store, fu_runner=followup_runner({"A-001": [A1_DONE, A1_DONE]}, log),
             fv={"A-001": ["REJECT", "PASS"]})
    assert log == ["A-001", "A-001"]
    out = r.followup_outcomes[0]
    assert (out.outcome, out.followup_calls, out.review_calls) == ("applied", 2, 2)
    assert store.get("A-001")["status"] == "완료 후보"


# 14. ReviewOutputError isolation — 해당 Action만 fail-closed, 신규 Action은 계속
def test_review_output_error_isolated(store, new_action):
    store.create(_prev_action(new_action, "A-002", assignee="김대리", task="자료 정리"))
    fu = {"A-001": A1_DONE, "A-002": _followup("A-002", TS_DONE, "김대리", DONE_TEXT)}

    def reviewer(prompt):
        ref = json.JSONDecoder().raw_decode(prompt.split("## INPUT\n", 1)[1])[0]["followup_result"]["action_id"]
        if ref == "A-001":
            return "garbage"
        return json.dumps({"target": {"type": "followup", "ref": ref}, "verdict": "PASS", "reasons": [], "attempt": 0})

    r = orch(store, fu=fu, fv_runner=reviewer)
    outs = {o.action_id: o for o in r.followup_outcomes}
    assert outs["A-001"].outcome == "rejected" and "fail-closed" in outs["A-001"].detail
    assert store.get("A-001")["status"] == "신규"
    assert outs["A-002"].outcome in ("applied", "reassessed")  # 다른 Previous Action은 계속 진행
    assert store.get("A-003")["task"] == "검토 의견 정리해서 공유"  # 신규 분석은 계속, ID 연속


# 15/16. Agent 완료 불가, Human Review만 완료 가능
def test_agent_never_completes_only_human(store, approval):
    orch(store)
    assert store.get("A-001")["status"] == "완료 후보"
    review_action(store, "A-001", "완료", actor_name="팀장", meeting_id=CUR, timestamp=TS_DONE,
                  approved_at="2026-11-17T10:00:00")
    assert store.get("A-001")["status"] == "완료"


# 17. concurrent store write 없음 — 병렬 구간에서 store write 호출 0, 쓰기는 모두 main thread
def test_no_store_write_during_parallel_phase(store, monkeypatch):
    main = threading.get_ident()
    writes = []
    orig = ActionStore._write

    def spy(self, actions):
        writes.append(threading.get_ident())
        return orig(self, actions)

    monkeypatch.setattr(ActionStore, "_write", spy)
    inner_a = analyst([_candidate()])

    def a_runner(prompt):
        assert writes == []  # Branch 실행 중 write 없음
        return inner_a(prompt)

    inner_f = followup_runner({"A-001": A1_DONE})

    def f_runner(prompt):
        assert writes == []
        return inner_f(prompt)

    orch(store, analyst_runner=a_runner, fu_runner=f_runner)
    assert writes and set(writes) == {main}


# 18. 기존 Action ID 연속성
def test_action_id_continuity(store, new_action):
    store.create(_prev_action(new_action, "A-002", assignee="김대리", task="자료 정리"))
    orch(store, fu={"A-001": A1_DONE, "A-002": _followup("A-002", TS_DONE, "김대리", DONE_TEXT, status="진행중")},
         fv={"A-001": "PASS", "A-002": "PASS"})
    assert [a["action_id"] for a in store.load_all()] == ["A-001", "A-002", "A-003"]


# 19. snapshot create-only
def test_snapshot_create_only(store, tmp_path):
    md = tmp_path / "meetings"
    r = orch(store, meetings_dir=md)
    assert r.snapshot_path == md / f"{CUR}.followups.json"
    before = r.snapshot_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        orch(store, meetings_dir=md)
    assert r.snapshot_path.read_text(encoding="utf-8") == before


def test_previous_action_original_fields_preserved(store):
    before = store.get("A-001")
    orch(store)
    after = store.get("A-001")
    for k in ("action_id", "meeting_id", "assignee", "task", "due_date", "due_date_normalized"):
        assert after[k] == before[k]


# 20. 기존 run_meeting_pipeline regression — 같은 입력에서 같은 최종 결과
def test_same_final_state_as_run_meeting_pipeline(store_path, new_action, tmp_path):
    def build(name):
        s = ActionStore(tmp_path / name / "actions.json")
        s.create(_prev_action(new_action))
        return s

    s1, s2 = build("seq"), build("par")
    run_meeting_pipeline(
        TRANSCRIPT, analyst_runner=analyst([_candidate()]), candidate_reviewer_runner=candidate_reviewer(),
        followup_runner=followup_runner({"A-001": A1_DONE}), followup_reviewer_runner=followup_reviewer({"A-001": "PASS"}),
        store=s1, metadata=META)
    orch(s2)

    def strip(actions):  # 시각(history ts)은 실행마다 달라진다
        return json.loads(json.dumps(actions, ensure_ascii=False), object_hook=lambda d: {
            k: v for k, v in d.items() if k not in ("timestamp_at", "at", "changed_at", "created_at", "updated_at")})

    a, b = s1.load_all(), s2.load_all()
    assert [(x["action_id"], x["status"], x["assignee"], x["task"], x["due_date"], x["due_date_normalized"]) for x in a] == \
           [(x["action_id"], x["status"], x["assignee"], x["task"], x["due_date"], x["due_date_normalized"]) for x in b]
