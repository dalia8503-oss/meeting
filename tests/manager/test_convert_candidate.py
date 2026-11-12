"""T-03-12: 후보 → Action 변환 통합 TEST (기존 모듈 연결 검증).

하위 모듈(T-03-4/5/9/10/11)에서 검증한 variation은 반복하지 않는다.
speaker·mentions는 Candidate 필드가 아니라 변환 context(utterance, mentions)다.
"""
import copy

import pytest

from meeting_action_agent.manager import ConversionContextError, convert_candidate
from meeting_action_agent.store import ActionStore

TEXT = "민수 씨 생산성 자료 다시 확인해서 목요일 오전까지 주세요."


def _meta(meeting_id="20261110-01", date="2026-11-10", participants=("팀장", "민수", "지영")):
    return {"meeting_id": meeting_id, "meeting_date": date, "participants": list(participants),
            "speaker_map": {"SPEAKER_00": {"name": "팀장"}}}


def _cand(**kw):
    c = {
        "meeting_id": "20261110-01", "assigner": "팀장", "assignee": "민수",
        "task": "생산성 자료 재확인", "due_date": "목요일 오전", "due_date_normalized": None,
        "evidence": TEXT, "timestamp": "00:12:31", "status": "신규", "flags": [],
    }
    c.update(kw)
    return c


def _utt(**kw):
    u = {"speaker": "팀장", "timestamp": "00:12:31", "text": TEXT}
    u.update(kw)
    return u


def _run(store, cand=None, utt=None, **kw):
    kw.setdefault("metadata", _meta())
    return convert_candidate(cand or _cand(), utterance=utt or _utt(), store=store, **kw)


@pytest.fixture
def store(store_path):
    return ActionStore(store_path)


def test_normal_candidate_creates_action(store):
    cand = _cand()
    original = copy.deepcopy(cand)
    res = _run(store, cand)
    assert [o.kind for o in res.outcomes] == ["created"]
    a = store.get("A-001")
    assert (a["assignee"], a["due_date"], a["due_date_normalized"]) == ("민수", "목요일 오전까지", "2026-11-12")  # 원문 복원(Step A)
    assert a["status"] == "신규" and a["flags"] == [] and a["extra_evidence"] == []
    assert a["evidence"] == TEXT and a["timestamp"] == "00:12:31"
    assert a["field_source"] == {} and a["approved_by_human"] is None
    h = a["history"]
    assert len(h) == 1 and h[0]["actor"] == "Agent" and h[0]["from_status"] is None and h[0]["to_status"] == "신규"
    assert cand == original  # 원본 후보는 수정하지 않는다


def test_assignee_needs_confirmation(store):
    utt = _utt(text="이거 그분이 해주세요.")
    _run(store, _cand(assignee="확인 필요", evidence=utt["text"]), utt)
    a = store.get("A-001")
    assert a["assignee"] == "확인 필요" and a["status"] == "확인 필요"


def test_due_date_needs_confirmation(store):
    _run(store, _cand(due_date="확인 필요"))
    a = store.get("A-001")
    assert a["due_date"] == "확인 필요" and a["due_date_normalized"] is None and a["status"] == "확인 필요"


def test_candidate_needs_confirmation_propagates_over_rule_result_new(store):
    # 규칙 결과는 '신규'(담당자·기한 정상)여도 candidate가 확인 필요면 낮추지 않는다.
    _run(store, _cand(status="확인 필요"))
    a = store.get("A-001")
    assert a["status"] == "확인 필요"
    assert a["history"][0]["to_status"] == "확인 필요"


def test_candidate_new_but_rules_need_confirmation_is_needs_confirmation(store):
    # candidate는 '신규'여도 기한 규칙에서 확인 필요가 나오면 최종은 확인 필요.
    _run(store, _cand(status="신규", due_date="확인 필요"))
    assert store.get("A-001")["status"] == "확인 필요"


def test_multiple_assignees_split_with_joint_flag(store):
    text = "민수 씨, 지영 씨 같이 생산성 자료 확인해 주세요."
    res = _run(store, _cand(evidence=text, due_date="미정"), _utt(text=text), mentions=["민수", "지영"])
    assert [o.kind for o in res.outcomes] == ["created", "created"]
    actions = store.load_all()
    assert [(a["action_id"], a["assignee"]) for a in actions] == [("A-001", "민수"), ("A-002", "지영")]
    for a in actions:
        assert "공동" in a["flags"] and "공동" in a["flag_notes"] and a["status"] == "신규"
        assert a["evidence"] == text and a["timestamp"] == "00:12:31"


def test_same_meeting_duplicate_adds_extra_evidence_only(store):
    _run(store)
    text2 = "민수 씨 그 자료 꼭 부탁해요."
    res = _run(store, _cand(timestamp="00:15:00", evidence=text2), _utt(timestamp="00:15:00", text=text2))
    assert [(o.kind, o.action_id) for o in res.outcomes] == [("duplicate", "A-001")]
    assert len(store.load_all()) == 1
    a = store.get("A-001")
    assert a["evidence"] == TEXT and a["timestamp"] == "00:12:31"
    assert a["extra_evidence"] == [
        {"meeting_id": "20261110-01", "timestamp": "00:15:00", "speaker": "팀장", "text": text2}
    ]


def test_previous_meeting_remention_creates_nothing(store):
    _run(store)
    before = store.get("A-001")
    res = _run(store, _cand(meeting_id="20261117-01"), metadata=_meta("20261117-01", "2026-11-17"))
    assert [(o.kind, o.action_id) for o in res.outcomes] == [("remention", "A-001")]
    assert len(store.load_all()) == 1 and store.get("A-001") == before  # 상태·extra_evidence 불변


def test_reviewer_failed_adds_flag_and_needs_confirmation(store):
    cand = _cand()
    _run(store, cand, reviewer_passed=False)
    a = store.get("A-001")
    assert "Reviewer미통과" in a["flags"] and a["status"] == "확인 필요"
    assert cand["flags"] == []


@pytest.mark.parametrize("utt", [
    {"timestamp": "00:12:31", "text": TEXT},                          # speaker 누락
    {"speaker": "팀장", "text": TEXT},                                 # timestamp 누락
    {"speaker": "팀장", "timestamp": "00:12:31"},                      # text 누락
    {"speaker": "팀장", "timestamp": "00:13:00", "text": TEXT},        # candidate.timestamp 충돌
])
def test_missing_or_conflicting_utterance_raises_and_stores_nothing(store, utt):
    with pytest.raises(ConversionContextError):
        convert_candidate(_cand(), utterance=utt, metadata=_meta(), store=store)
    assert store.load_all() == []


def test_due_raw_restored_from_utterance_and_relative_week_normalized(store):
    text = "박 대리님이 다음주 화요일까지 검토 의견 정리해서 공유해 주세요."
    _run(store, _cand(assignee="민수", due_date="다음주 화요일", evidence=text),
         utt=_utt(text=text), metadata=_meta(date="2026-11-17"))
    a = store.get("A-001")
    assert (a["due_date"], a["due_date_normalized"]) == ("다음주 화요일까지", "2026-11-24")


def test_due_raw_not_invented_when_not_in_utterance(store):
    text = "민수 씨 가능하면 다음주쯤 자료 확인해 주세요."
    _run(store, _cand(due_date="다음주 화요일", evidence=text), utt=_utt(text=text))
    a = store.get("A-001")
    assert a["due_date"] == "다음주 화요일"  # 발언에 없는 표현을 만들지 않는다
