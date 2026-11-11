"""T-05-2/3 vertical slice: Transcript → MeetingAnalysis(stub runner) → 재연결 → convert_candidate → A-001 저장.

실제 LLM 호출 없음. 모델 품질이 아니라 코드 경로를 검증한다.
"""
import json

import pytest

from meeting_action_agent.analyst import AnalysisValidationError, relink_utterance
from meeting_action_agent.pipeline import extract_and_convert
from meeting_action_agent.store import ActionStore

MID = "20261110-01"
SENT = "김대리, 금요일까지 도면 수정해서 공유해주세요."
TRANSCRIPT = [
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:01:00", "text": "도면 검토 결과를 공유합니다."},
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:02:10", "text": SENT},
]
META = {"meeting_id": MID, "meeting_date": "2026-11-10", "participants": ["팀장", "김대리"]}


def _analysis(candidates):
    return {
        "meeting_id": MID,
        "key_discussions": [{"text": "도면 검토 결과 공유", "evidence_timestamps": ["00:01:00"]}],
        "decisions": [], "open_issues": [], "needs_confirmation": [],
        "action_candidates": candidates,
    }


GOLDEN = {
    "instruction_type": "지시·요청",
    "candidate": {
        "meeting_id": MID, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정해서 공유",
        "due_date": "금요일까지", "due_date_normalized": None, "evidence": SENT,
        "timestamp": "00:02:10", "status": "신규", "flags": [],
    },
}


@pytest.fixture
def store(store_path):
    return ActionStore(store_path)


def test_golden_sentence_becomes_A001(store):
    runner = lambda prompt: json.dumps(_analysis([GOLDEN]), ensure_ascii=False)
    result = extract_and_convert(TRANSCRIPT, runner, store=store, metadata=META)

    assert [(o.kind, o.action_id) for o in result.outcomes] == [("created", "A-001")]
    a = store.get("A-001")
    assert a["assignee"] == "김대리" and a["assigner"] == "팀장"
    assert a["task"] == "도면 수정해서 공유"
    assert a["due_date"] == "금요일까지"
    assert a["due_date_normalized"] == "2026-11-13"  # 회의일(화) 이후 첫 금요일
    assert a["status"] == "신규"
    # evidence/timestamp는 원본 Utterance에서 온다
    assert a["evidence"] == SENT and a["timestamp"] == "00:02:10" and a["meeting_id"] == MID


def test_opinion_only_creates_no_action(store):
    runner = lambda prompt: _analysis([])
    result = extract_and_convert(TRANSCRIPT, runner, store=store, metadata=META)
    assert result.outcomes == ()
    assert store.load_all() == []


def test_relink_zero_and_duplicate():
    with pytest.raises(AnalysisValidationError, match="없음"):
        relink_utterance({"meeting_id": MID, "timestamp": "00:09:00"}, TRANSCRIPT)
    dup = TRANSCRIPT + [{**TRANSCRIPT[1], "speaker": "지영", "text": "네."}]
    with pytest.raises(AnalysisValidationError, match="특정할 수 없음"):
        relink_utterance({"meeting_id": MID, "timestamp": "00:02:10"}, dup)
    assert relink_utterance({"meeting_id": MID, "timestamp": "00:02:10"}, TRANSCRIPT)["text"] == SENT


# ── T-05-1/3/4/6/9: 최소 규칙 (stub runner, 실제 LLM 아님) ─────────────────────────

def _run(store, transcript, candidates, meta=META):
    analysis = _analysis(candidates)
    analysis["key_discussions"] = [{"text": "논의", "evidence_timestamps": [transcript[0]["timestamp"]]}]
    return extract_and_convert(transcript, lambda p: analysis, store=store, metadata=meta)


def _cand(itype, ts, text, **kw):
    c = {"meeting_id": MID, "assigner": "팀장", "assignee": "확인 필요", "task": "확인",
         "due_date": "확인 필요", "due_date_normalized": None, "evidence": text,
         "timestamp": ts, "status": "신규", "flags": []}
    c.update(kw)
    return {"instruction_type": itype, "candidate": c}


def test_us02_example_minsu(store):
    text = "민수 씨, 생산성 자료 다시 확인해서 목요일 오전까지 주세요."
    tr = [{"meeting_id": MID, "speaker": "팀장", "timestamp": "00:12:31", "text": text}]
    meta = {**META, "participants": ["팀장", "민수"]}
    c = _cand("지시·요청", "00:12:31", text, assignee="민수", task="생산성 자료 재확인", due_date="목요일 오전까지")
    _run(store, tr, [c], meta)
    a = store.get("A-001")
    assert (a["assignee"], a["task"], a["timestamp"], a["status"]) == ("민수", "생산성 자료 재확인", "00:12:31", "신규")
    assert a["due_date"] == "목요일 오전까지"
    assert a["due_date_normalized"].startswith("2026-11-12")


def test_us03_unclear_assignee_and_due_is_needs_confirmation(store):
    text = "이거 다음번까지 확인해야겠네요."
    tr = [{"meeting_id": MID, "speaker": "팀장", "timestamp": "00:03:00", "text": text}]
    c = _cand("자발적 약속", "00:03:00", text, due_date="다음번까지", status="확인 필요")
    _run(store, tr, [c])
    a = store.get("A-001")
    assert a["assignee"] == "확인 필요" and a["status"] == "확인 필요"
    assert a["due_date_normalized"] is None


def test_explicit_undecided_due_is_mijeong_not_confirmation(store):
    text = "김대리, 기한은 아직 안 정했지만 도면 정리해 주세요."
    tr = [{"meeting_id": MID, "speaker": "팀장", "timestamp": "00:04:00", "text": text}]
    c = _cand("지시·요청", "00:04:00", text, assignee="김대리", task="도면 정리", due_date="미정")
    _run(store, tr, [c])
    a = store.get("A-001")
    assert a["due_date"] == "미정" and a["status"] == "신규"


def test_zero_instruction_meeting_is_normal(store):
    tr = [
        {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:01:00", "text": "이번 주 매출이 올랐습니다."},
        {"meeting_id": MID, "speaker": "김대리", "timestamp": "00:01:30", "text": "한번 확인해 보면 어떨까요?"},
    ]
    analysis = _analysis([])
    analysis["open_issues"] = [{"text": "확인 여부 제안", "evidence_timestamps": ["00:01:30"]}]
    result = extract_and_convert(tr, lambda p: analysis, store=store, metadata=META)
    assert result.outcomes == () and store.load_all() == []


def test_conditional_and_ambiguous_become_needs_confirmation(store):
    tr = [
        {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:05:00", "text": "김대리, 견적 오면 검토합시다."},
        {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:06:00", "text": "김대리, 그 건은 좀 봐주면 좋겠네요."},
    ]
    cond = _cand("조건부", "00:05:00", tr[0]["text"], assignee="김대리", task="견적 검토", due_date="미정",
                 status="확인 필요", flags=["조건부"], flag_notes={"조건부": {"note": "견적 도착 시"}})
    amb = _cand("지시여부애매", "00:06:00", tr[1]["text"], assignee="김대리", task="해당 건 확인", due_date="미정",
                status="확인 필요", flags=["지시여부애매"])
    _run(store, tr, [cond, amb])
    acts = store.load_all()
    assert [a["status"] for a in acts] == ["확인 필요", "확인 필요"]
    assert "조건부" in acts[0]["flags"] and "지시여부애매" in acts[1]["flags"]


def test_type_flag_mismatch_rejected(store):
    from meeting_action_agent.analyst import validate_analysis
    bad = _cand("조건부", "00:02:10", SENT, assignee="김대리", due_date="미정")  # flag 없이 신규
    with pytest.raises(Exception):
        validate_analysis(_analysis([bad]), TRANSCRIPT)
    bad2 = _cand("지시여부애매", "00:02:10", SENT, assignee="김대리", due_date="미정", status="확인 필요")  # flag 누락
    with pytest.raises(AnalysisValidationError, match="flags"):
        validate_analysis(_analysis([bad2]), TRANSCRIPT)


# ── T-05-8: pipeline 경로 보강 (복수 담당자 묶기, extra_evidence 재연결) ──────────────

def test_pipeline_multi_assignee_and_extra_evidence(store):
    t1 = "민수 씨, 지영 씨 같이 생산성 자료 확인해 주세요."
    t2 = "기한은 금요일까지입니다."
    tr = [
        {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:10:00", "text": t1},
        {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:10:20", "text": t2},
    ]
    meta = {**META, "participants": ["팀장", "민수", "지영"]}
    extra = [{"meeting_id": MID, "timestamp": "00:10:20", "speaker": "조작", "text": "조작"}]
    cands = [_cand("지시·요청", "00:10:00", t1, assignee=n, task="생산성 자료 확인", due_date="금요일까지",
                   extra_evidence=extra) for n in ("민수", "지영")]
    result = _run(store, tr, cands, meta)
    assert [o.kind for o in result.outcomes] == ["created", "created"]
    acts = store.load_all()
    assert [a["assignee"] for a in acts] == ["민수", "지영"]
    for a in acts:
        assert "공동" in a["flags"]
        # extra_evidence의 speaker/text는 원본에서 재연결된다
        assert a["extra_evidence"] == [{"meeting_id": MID, "timestamp": "00:10:20", "speaker": "팀장", "text": t2}]
