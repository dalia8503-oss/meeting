"""T-09-11 이행점검 Pipeline 연결 (+T-09-8 acceptance, T-09-10 flag 통합). fake deterministic runner만 사용한다.

2차 회의 하나에서 '이전 Action 점검'과 '새 Action 생성'이 함께 이루어지는지 검증한다. 실제 LLM 호출 없음.
"""
import copy
import json

import pytest

from meeting_action_agent.pipeline import run_meeting_pipeline
from meeting_action_agent.rules.due_date import decide_due_date
from meeting_action_agent.state import CompletionGuardError
from meeting_action_agent.store import ActionStore

PREV = "20261110-01"
CUR = "20261117-01"
META = {"meeting_id": CUR, "meeting_date": "2026-11-17", "participants": ["팀장", "김대리", "박대리"]}

Q_TEXT = "김 대리, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?"
DONE_TEXT = "수정 완료해서 어제 공유했습니다."
NEW_TEXT = "좋습니다. 그러면 이번에는 박 대리님이 다음주 화요일까지 검토 의견 정리해서 공유해 주세요."
PROPOSAL = "다음에는 품질 문제도 한번 보면 좋겠습니다."
QUESTION = "도면 검토는 끝났나요?"

TS_Q, TS_DONE, TS_NEW, TS_ACK, TS_PROP, TS_QUES = (
    "00:01:00", "00:01:10", "00:01:30", "00:01:40", "00:02:00", "00:02:10")
TRANSCRIPT = [
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": TS_Q, "text": Q_TEXT},
    {"meeting_id": CUR, "speaker": "김대리", "timestamp": TS_DONE, "text": DONE_TEXT},
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": TS_NEW, "text": NEW_TEXT},
    {"meeting_id": CUR, "speaker": "박대리", "timestamp": TS_ACK, "text": "네, 알겠습니다."},
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": TS_PROP, "text": PROPOSAL},
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": TS_QUES, "text": QUESTION},
]


def _prev_action(base, action_id="A-001", *, assignee="김대리", task="도면 수정해서 공유", due="금요일",
                 due_norm="2026-11-13"):
    a = copy.deepcopy(base)
    a.update(action_id=action_id, meeting_id=PREV, assignee=assignee, task=task, due_date=due,
             due_date_normalized=due_norm, status="신규")
    a["history"][0]["to_status"] = "신규"
    return a


def _followup(action_id, ts, speaker, text, status="완료 후보", conf="상", **extra):
    r = {"meeting_id": CUR, "action_id": action_id, "proposed_status": status, "confidence": conf,
         "evidence": [{"meeting_id": CUR, "timestamp": ts, "speaker": speaker, "text": text}] if text else [],
         "flags_to_add": [], "reason": "테스트 판정"}
    r.update(extra)
    return r


A1_DONE = _followup("A-001", TS_DONE, "김대리", DONE_TEXT)


def _candidate(ts=TS_NEW, text=NEW_TEXT, assignee="박대리", task="검토 의견 정리해서 공유", due="다음주 화요일까지"):
    return {"instruction_type": "지시·요청", "candidate": {
        "meeting_id": CUR, "assigner": "팀장", "assignee": assignee, "task": task, "due_date": due,
        "due_date_normalized": None, "evidence": text, "timestamp": ts, "status": "신규", "flags": []}}


def _analysis(candidates):
    return {"meeting_id": CUR,
            "key_discussions": [{"text": "도면 점검", "evidence_timestamps": [TS_DONE]}],
            "decisions": [], "open_issues": [], "needs_confirmation": [], "action_candidates": candidates}


def analyst(candidates):
    return lambda prompt: json.dumps(_analysis(candidates), ensure_ascii=False)


def _json_after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def _seq(values):
    return {k: iter(v if isinstance(v, list) else [v]) for k, v in values.items()}


def followup_runner(results, log=None):
    """action_id별 FollowupResult (재분석이 있으면 리스트의 다음 값)."""
    seq = _seq(results)

    def runner(prompt):
        aid = _json_after(prompt, "## INPUT\n")["action"]["action_id"]
        if log is not None:
            log.append(aid)
        return json.dumps(next(seq[aid]), ensure_ascii=False)
    return runner


def followup_reviewer(verdicts, log=None):
    seq = _seq(verdicts)

    def runner(prompt):
        ref = _json_after(prompt, "## INPUT\n")["followup_result"]["action_id"]
        if log is not None:
            log.append(ref)
        v = next(seq[ref])
        reasons = [] if v == "PASS" else [{"code": "판정비도출", "detail": "근거 불충분"}]
        return json.dumps({"target": {"type": "followup", "ref": ref}, "verdict": v, "reasons": reasons, "attempt": 0})
    return runner


def candidate_reviewer(verdict="PASS"):
    def runner(prompt):
        ts = _json_after(prompt, "## ActionCandidate\n")["timestamp"]
        reasons = [] if verdict == "PASS" else [{"code": "허위담당자", "detail": "제안/질문"}]
        return json.dumps({"target": {"type": "action", "ref": ts}, "verdict": verdict, "reasons": reasons, "attempt": 0})
    return runner


@pytest.fixture
def store(store_path, new_action):
    s = ActionStore(store_path)
    s.create(_prev_action(new_action))
    return s


def go(store, *, fu=None, fv=None, cands=None, cv="PASS", meetings_dir=None, transcript=TRANSCRIPT,
       analyst_runner=None, fu_log=None):
    fu = fu if fu is not None else {"A-001": A1_DONE}
    fv = fv if fv is not None else {"A-001": "PASS"}
    cands = [_candidate()] if cands is None else cands
    return run_meeting_pipeline(
        transcript, analyst_runner=analyst_runner or analyst(cands), candidate_reviewer_runner=candidate_reviewer(cv),
        followup_runner=followup_runner(fu, fu_log), followup_reviewer_runner=followup_reviewer(fv),
        store=store, metadata=META, meetings_dir=meetings_dir)


# 1~4, T-09-8 acceptance, T-09-11 통합
def test_integration_a001_candidate_and_a002_new(store):
    r = go(store)
    a1, a2 = store.get("A-001"), store.get("A-002")
    assert a1["status"] == "완료 후보"
    assert (a2["assignee"], a2["task"], a2["due_date"], a2["status"]) == (
        "박대리", "검토 의견 정리해서 공유", "다음주 화요일까지", "신규")
    # 기한 환산은 기존 규칙(decide_due_date)의 몫 — Pipeline이 바꾸지 않는다 ("다음주 화요일"은 현재 규칙상 미환산)
    assert a2["due_date_normalized"] == decide_due_date(raw="다음주 화요일까지", meeting_date="2026-11-17").due_date_normalized
    assert a2["meeting_id"] == CUR
    assert [o.outcome for o in r.followup_outcomes] == ["applied"]
    assert [(o.kind, o.action_id) for o in r.new_actions.conversion.outcomes] == [("created", "A-002")]
    assert [x["action_id"] for x in store.load_all()] == ["A-001", "A-002"]  # ID 연속, 재사용/덮어쓰기 없음


# 5, 18. A-001 원본 보존·due immutable
def test_previous_action_original_fields_preserved(store):
    before = store.get("A-001")
    go(store)
    after = store.get("A-001")
    for k in ("assignee", "task", "due_date", "due_date_normalized", "evidence", "timestamp", "meeting_id",
              "assigner", "extra_evidence", "approved_by_human"):
        assert after[k] == before[k], k
    assert after["history"][:1] == before["history"] and len(after["history"]) == 2


# 6~8. Evidence 정확성·분리
def test_evidence_separated(store):
    go(store)
    a1, a2 = store.get("A-001"), store.get("A-002")
    assert [e["text"] for e in a1["history"][-1]["evidence"]] == [DONE_TEXT]
    assert a2["evidence"] == NEW_TEXT and a2["timestamp"] == TS_NEW
    assert DONE_TEXT not in a2["evidence"] and NEW_TEXT not in json.dumps(a1["history"], ensure_ascii=False)
    assert all(DONE_TEXT != e["text"] for e in a2.get("extra_evidence", []))


# 9. Agent 완료 금지
def test_agent_never_creates_done(store):
    go(store)
    assert all(a["status"] != "완료" for a in store.load_all())


def test_done_proposal_is_refused_and_nothing_written(store):
    bad = {"A-001": _followup("A-001", TS_DONE, "김대리", DONE_TEXT, status="완료")}
    with pytest.raises(CompletionGuardError):
        go(store, fu=bad)
    assert len(store.load_all()) == 1 and store.get("A-001")["status"] == "신규"


# 17. 완료 후보 lock
def test_done_candidate_locked_on_second_followup(store):
    go(store)  # A-001 완료 후보
    n = len(store.get("A-001")["history"])
    fu = {"A-001": _followup("A-001", TS_DONE, "김대리", DONE_TEXT, status="진행중", conf="중")}
    go(store, fu=fu, cands=[])
    a = store.get("A-001")
    assert a["status"] == "완료 후보" and a["history"][-1].get("kind") == "재판정" and len(a["history"]) == n + 1


# 10, 11. 제안·질문 제외
def test_proposal_and_question_make_no_actions(store):
    go(store)
    assert {a["action_id"] for a in store.load_all()} == {"A-001", "A-002"}
    assert all(PROPOSAL not in a["evidence"] and QUESTION not in a["evidence"] for a in store.load_all())


@pytest.mark.parametrize("ts,text", [(TS_PROP, PROPOSAL), (TS_QUES, QUESTION)])
def test_wrongly_extracted_proposal_or_question_rejected_by_reviewer(store, ts, text):
    r = go(store, cands=[_candidate(ts=ts, text=text)], cv="REJECT")
    assert len(store.load_all()) == 1 and len(r.new_actions.rejected) == 1


# 12. Follow-up REJECT + 신규 성공
def test_followup_reject_does_not_block_new_action(store):
    r = go(store, fv={"A-001": ["REJECT", "REJECT"]}, fu={"A-001": [A1_DONE, A1_DONE]})
    a1 = store.get("A-001")
    assert a1["status"] == "신규" and len(a1["history"]) == 1
    assert [o.outcome for o in r.followup_outcomes] == ["rejected"]
    assert [o.action_id for o in r.followup_rejected] == ["A-001"]
    assert store.get("A-002")["status"] == "신규"


# 13. Follow-up PASS + 신규 0건 / invalid
def test_followup_kept_when_no_new_candidates(store):
    r = go(store, cands=[])
    assert store.get("A-001")["status"] == "완료 후보" and len(store.load_all()) == 1
    assert r.new_actions.conversion.outcomes == () and r.new_actions_error is None


def test_followup_kept_when_new_extraction_invalid(store):
    r = go(store, analyst_runner=lambda prompt: "not json")
    assert store.get("A-001")["status"] == "완료 후보"
    assert r.new_actions is None and "JSON" in r.new_actions_error
    assert [o.outcome for o in r.followup_outcomes] == ["applied"]
    assert len(store.load_all()) == 1


# 14. 미언급 Previous Action 불변
def test_unmentioned_previous_action_unchanged(store):
    before = store.get("A-001")
    fu = {"A-001": _followup("A-001", None, None, None, status="확인 필요", conf="하")}
    r = go(store, fu=fu)
    assert store.get("A-001") == before
    assert [o.outcome for o in r.followup_outcomes] == ["skipped"]


# 15. 여러 Previous Actions 독립 (한 건 REJECT가 다른 건 PASS를 막지 않음)
def test_multiple_previous_actions_independent(store, new_action):
    store.create(_prev_action(new_action, "A-002", assignee="이과장", task="자재 발주", due="목요일",
                              due_norm="2026-11-12"))
    prog = _followup("A-002", TS_DONE, "김대리", DONE_TEXT, status="진행중", conf="중")
    r = go(store, fu={"A-001": A1_DONE, "A-002": [prog, prog]},
           fv={"A-001": "PASS", "A-002": ["REJECT", "REJECT"]})
    assert store.get("A-001")["status"] == "완료 후보" and store.get("A-002")["status"] == "신규"
    assert {o.action_id: o.outcome for o in r.followup_outcomes} == {"A-001": "applied", "A-002": "rejected"}
    assert store.get("A-003")["assignee"] == "박대리"  # 신규는 다음 번호


# 16. CHK020: 담당자 확인 필요는 완료 후보 불가
def test_chk020_unknown_assignee_not_done_candidate(store_path, new_action):
    s = ActionStore(store_path)
    s.create(_prev_action(new_action, assignee="확인 필요"))
    s.change_status("A-001", "확인 필요", "Agent") if s.get("A-001")["status"] != "확인 필요" else None
    go(s)
    assert s.get("A-001")["status"] != "완료 후보"


# 19. retry 경로 통합: REJECT → 재분석 → PASS
def test_retry_path_integrated(store):
    first = _followup("A-001", TS_DONE, "김대리", DONE_TEXT, status="진행중", conf="중")
    log = []
    r = go(store, fu={"A-001": [first, A1_DONE]}, fv={"A-001": ["REJECT", "PASS"]}, fu_log=log)
    assert store.get("A-001")["status"] == "완료 후보"
    assert (r.followup_outcomes[0].followup_calls, r.followup_outcomes[0].review_calls) == (2, 2)
    assert log == ["A-001", "A-001"]
    assert store.get("A-002")["status"] == "신규"


# 20. flag 통합 (기한경과·기한변경제안)
def test_flags_stored_through_pipeline(store):
    res = _followup("A-001", TS_DONE, "김대리", DONE_TEXT, status="진행중", conf="중",
                    flags_to_add=["기한변경제안"], proposed_due_date="다음주 월요일")
    go(store, fu={"A-001": res})
    a1 = store.get("A-001")
    assert a1["due_date"] == "금요일" and a1["due_date_normalized"] == "2026-11-13"
    assert set(a1["flags"]) >= {"기한경과", "기한변경제안"}
    assert a1["flag_notes"]["기한변경제안"]["note"] == "다음주 월요일"
    assert "기한경과" in a1["flag_notes"]


# 21, 22. FollowupResult create-only, 동일 meeting 재실행 안전 실패
def test_followup_snapshot_create_only(store, tmp_path):
    mdir = tmp_path / "meetings"
    go(store, meetings_dir=mdir)
    snap = mdir / f"{CUR}.followups.json"
    saved = snap.read_text(encoding="utf-8")
    assert json.loads(saved)[0]["action_id"] == "A-001"
    n_actions, hist = len(store.load_all()), store.get("A-001")["history"]
    with pytest.raises(FileExistsError):
        go(store, meetings_dir=mdir)
    assert snap.read_text(encoding="utf-8") == saved  # overwrite 없음
    assert len(store.load_all()) == n_actions and store.get("A-001")["history"] == hist  # 재실행 시 쓰기 없음


# 이전 Action이 없는 첫 회의: follow-up 없이 신규 생성만
def test_no_previous_actions_only_new(store_path):
    s = ActionStore(store_path)
    r = go(s)
    assert r.followup_outcomes == () and [a["action_id"] for a in s.load_all()] == ["A-001"]


# 23. 기존 vertical slice는 tests/test_vertical_slice.py 가 그대로 통과해야 한다 (pipeline 기존 함수 미변경)
