"""T-08 Previous Action Follow-up (domain slice). 외부 LLM 없이 fake runner로 검증한다.

fake runner는 '좋은 LLM'의 기대 출력을 발언 텍스트 → 판정표로 고정한 것이다 (키워드 규칙 엔진이 아니다).
도메인 코드는 runner 출력을 검증(근거 실재·완료 가드·질문/부정 근거 차단·이름 추측 차단)만 한다.
"""
import copy
import json

import pytest

from meeting_action_agent.contracts import SchemaValidationError, validate
from meeting_action_agent.manager.followup import (
    FollowupValidationError,
    NoPreviousActionsSpecified,
    check_previous_actions,
    save_followups,
    select_followup_targets,
)
from meeting_action_agent.state import CompletionGuardError
from meeting_action_agent.store import ActionStore

PREV = "20261110-01"
CUR = "20261117-01"


def _action(base, action_id="A-001", meeting_id=PREV, assignee="김대리", task="도면 수정해서 공유"):
    a = copy.deepcopy(base)
    a.update(action_id=action_id, meeting_id=meeting_id, assignee=assignee, task=task,
             due_date="금요일", due_date_normalized=None, status="신규")
    return a


def _tr(text, speaker="김대리", ts="00:08:31", meeting_id=CUR):
    return [{"meeting_id": meeting_id, "speaker": speaker, "timestamp": ts, "text": text}]


# 발언 → (proposed_status, confidence, reason). 좋은 LLM의 기대 판정을 고정한 표.
EXPECTED = {
    "지난번 말씀하신 도면 수정해서 공유했습니다.": ("완료 후보", "상", "완료형 발언"),
    "도면 수정하고 있는데 아직 공유는 못했습니다.": ("진행중", "중", "진행 발언, 공유 미완"),
    "도면 수정은 아직 시작하지 못했습니다.": ("미완료", "상", "미수행 명시"),
    "도면 수정은 언제까지 하면 되나요?": ("확인 필요", "하", "질문이라 이행 근거 아님"),
    "도면 건은 처리했습니다.": ("확인 필요", "하", "어떤 도면인지 불명확"),
}


def _payload(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("## INPUT\n", 1)[1])[0]


def make_runner(table=EXPECTED, calls=None):
    def runner(prompt):
        if calls is not None:
            calls.append(prompt)
        data = _payload(prompt)
        action, transcript = data["action"], data["transcript"]
        for u in transcript:
            if u["text"] in table:
                status, conf, reason = table[u["text"]]
                ev = [{"meeting_id": u["meeting_id"], "timestamp": u["timestamp"],
                       "speaker": u["speaker"], "text": u["text"]}]
                return json.dumps({"meeting_id": u["meeting_id"], "action_id": action["action_id"],
                                   "proposed_status": status, "confidence": conf, "evidence": ev,
                                   "flags_to_add": [], "reason": reason}, ensure_ascii=False)
        return json.dumps({"meeting_id": transcript[0]["meeting_id"], "action_id": action["action_id"],
                           "proposed_status": "확인 필요", "confidence": "하", "evidence": [],
                           "flags_to_add": [], "reason": "신규 회의에서 해당 Action 언급 없음"},
                          ensure_ascii=False)
    return runner


@pytest.fixture
def store(store_path, new_action):
    s = ActionStore(store_path)
    s.create(_action(new_action))
    return s


def _run(store, transcript, runner=None, **kw):
    return check_previous_actions(transcript, runner or make_runner(), store=store, **kw)


# 1. previous Action 조회 / 2. 완료 Action 제외
def test_targets_exclude_current_meeting_and_done(store, new_action, approval):
    store.create(_action(new_action, "A-002", task="다른 일"))
    store.create(_action(new_action, "A-003", meeting_id=CUR, task="이번 회의 신규"))
    done = _action(new_action, "A-004", task="완료된 일")
    store.create(done)
    store.change_status("A-004", "진행중", "Agent")
    store.change_status("A-004", "완료 후보", "Agent")
    store.change_status("A-004", "완료", "사람", approved_by_human=approval)
    ids = [a["action_id"] for a in select_followup_targets(store, CUR)]
    assert ids == ["A-001", "A-002"]  # 현재 회의 Action·완료 Action 제외


def test_targets_include_non_done_statuses_incl_done_candidate(store, new_action):
    store.create(_action(new_action, "A-002", task="후보인 일"))
    store.change_status("A-002", "진행중", "Agent")
    store.change_status("A-002", "완료 후보", "Agent")
    assert {a["action_id"] for a in select_followup_targets(store, CUR)} == {"A-001", "A-002"}


def test_no_previous_actions_is_not_started(store_path):
    with pytest.raises(NoPreviousActionsSpecified):
        check_previous_actions(_tr("x"), make_runner(), store=ActionStore(store_path))


# 3~6, 8. 판정 케이스
@pytest.mark.parametrize("text,status,conf", [
    ("지난번 말씀하신 도면 수정해서 공유했습니다.", "완료 후보", "상"),
    ("도면 수정하고 있는데 아직 공유는 못했습니다.", "진행중", "중"),
    ("도면 수정은 아직 시작하지 못했습니다.", "미완료", "상"),
    ("도면 수정은 언제까지 하면 되나요?", "확인 필요", "하"),
    ("도면 건은 처리했습니다.", "확인 필요", "하"),
])
def test_cases(store, text, status, conf):
    report = _run(store, _tr(text))
    assert len(report.results) == 1 and not report.rejected
    r = report.results[0]
    assert (r["action_id"], r["proposed_status"], r["confidence"]) == ("A-001", status, conf)
    validate("followup-result", r)
    assert r["meeting_id"] == CUR  # 점검에 사용한 신규 회의


def test_unrelated_utterance_not_linked(store):
    report = _run(store, _tr("품질 문제 도면은 다음 회의에서 봅시다."))
    r = report.results[0]
    assert r["proposed_status"] == "확인 필요" and r["evidence"] == []


# 9. evidence 보존
def test_evidence_preserved_from_original(store):
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    r = _run(store, _tr(text, ts="00:08:31")).results[0]
    assert r["evidence"] == [{"meeting_id": CUR, "timestamp": "00:08:31", "speaker": "김대리", "text": text}]


def test_evidence_not_in_transcript_rejected(store):
    def runner(prompt):
        return json.dumps({
            "meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료 후보", "confidence": "상",
            "evidence": [{"meeting_id": CUR, "timestamp": "00:08:31", "speaker": "김대리",
                          "text": "도면 수정 끝냈습니다."}],  # 원문과 다른 text
            "flags_to_add": [], "reason": "r"}, ensure_ascii=False)
    report = _run(store, _tr("지난번 말씀하신 도면 수정해서 공유했습니다."), runner)
    assert report.results == () and len(report.rejected) == 1
    assert report.rejected[0]["action_id"] == "A-001"


# 10. speaker label 기반 이름 추측 금지
def test_speaker_name_guess_rejected(store):
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    transcript = _tr(text, speaker="speaker_1")

    def runner(prompt):  # LLM이 speaker_1을 김대리로 바꿔 쓴 경우
        return json.dumps({
            "meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료 후보", "confidence": "상",
            "evidence": [{"meeting_id": CUR, "timestamp": "00:08:31", "speaker": "김대리", "text": text}],
            "flags_to_add": [], "reason": "r"}, ensure_ascii=False)
    report = _run(store, transcript, runner)
    assert report.results == () and len(report.rejected) == 1


def test_unmapped_speaker_label_cannot_be_high_confidence(store):
    # speaker_1 발언만으로 담당자 본인으로 확신(상)할 수 없다. 이름이 텍스트에 없으므로.
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    report = _run(store, _tr(text, speaker="speaker_1"))  # fake는 '상'을 낸다
    assert report.results == () and len(report.rejected) == 1


def test_unmapped_speaker_label_completion_candidate_rejected(store):
    # T-LIVE-SPK: 사용자 확인 화자(speaker_map)가 없으면 이름 없는 완료 발언은 중간 확신도여도 완료 후보 불가 (fail-closed)
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    runner = make_runner({text: ("완료 후보", "중", "화자 미확정")})
    report = _run(store, _tr(text, speaker="speaker_1"), runner)
    assert report.results == () and len(report.rejected) == 1


def test_assignee_name_in_text_allows_high_confidence(store):
    text = "김대리가 도면 수정해서 공유했습니다."
    runner = make_runner({text: ("완료 후보", "상", "텍스트에 담당자 명시")})
    r = _run(store, _tr(text, speaker="speaker_2"), runner).results[0]
    assert r["confidence"] == "상"


# 완료 가드 / 질문·부정 근거 차단
def test_agent_cannot_output_done(store):
    def runner(prompt):
        return json.dumps({"meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료",
                           "confidence": "상", "evidence": [], "flags_to_add": [], "reason": "r"})
    with pytest.raises(CompletionGuardError):
        _run(store, _tr("도면 수정 완료했습니다."), runner)


@pytest.mark.parametrize("text", [
    "도면 수정은 언제까지 하면 되나요?",
    "도면 수정은 아직 못했습니다.",
])
def test_done_candidate_on_question_or_negation_rejected(store, text):
    runner = make_runner({text: ("완료 후보", "상", "잘못된 LLM 판정")})
    report = _run(store, _tr(text), runner)
    assert report.results == () and len(report.rejected) == 1


def test_wrong_action_id_or_meeting_id_rejected(store):
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    good = json.loads(make_runner()("## INPUT\n" + json.dumps({
        "action": {"action_id": "A-001"}, "transcript": _tr(text)}, ensure_ascii=False)))
    for patch in ({"action_id": "A-999"}, {"meeting_id": PREV}):
        bad = {**good, **patch}
        report = _run(store, _tr(text), lambda p, b=bad: json.dumps(b, ensure_ascii=False))
        assert report.results == () and len(report.rejected) == 1


def test_invalid_json_or_schema_rejected(store):
    assert _run(store, _tr("x"), lambda p: "not json").rejected
    bad = {"meeting_id": CUR, "action_id": "A-001", "proposed_status": "진행중", "confidence": "중",
           "evidence": [], "flags_to_add": [], "reason": "근거 없는 진행중"}  # 스키마: 근거 필수
    assert _run(store, _tr("x"), lambda p: json.dumps(bad)).rejected


# 다중 Action: 독립 판정, 한 건 실패가 다른 건을 막지 않음
def test_multiple_actions_independent(store, new_action):
    store.create(_action(new_action, "A-002", assignee="이과장", task="품질 도면 검토"))
    text = "지난번 말씀하신 도면 수정해서 공유했습니다."
    inner = make_runner()

    def runner(prompt):
        if _payload(prompt)["action"]["action_id"] == "A-002":
            raise_bad = {"meeting_id": CUR, "action_id": "A-002", "proposed_status": "진행중",
                         "confidence": "중", "evidence": [], "flags_to_add": [], "reason": "bad"}
            return json.dumps(raise_bad)
        return inner(prompt)
    report = _run(store, _tr(text), runner)
    assert [r["action_id"] for r in report.results] == ["A-001"]
    assert [r["action_id"] for r in report.rejected] == ["A-002"]


# 12. Action 불변 + 원본 불변
def test_actions_and_transcript_unchanged(store, store_path):
    before = store_path.read_bytes()
    transcript = _tr("지난번 말씀하신 도면 수정해서 공유했습니다.")
    snapshot = copy.deepcopy(transcript)
    report = _run(store, transcript)
    assert report.results[0]["proposed_status"] == "완료 후보"
    assert store_path.read_bytes() == before  # 파일 그대로
    assert store.get("A-001")["status"] == "신규"
    assert len(store.get("A-001")["history"]) == 1
    assert transcript == snapshot


def test_runner_is_injected_and_called_per_action(store):
    calls = []
    _run(store, _tr("도면 건은 처리했습니다."), make_runner(calls=calls))
    assert len(calls) == 1 and "도면 수정해서 공유" in calls[0]


# 11/14. schema validation + 저장
def test_save_followups_create_only_and_validated(store, tmp_path):
    report = _run(store, _tr("지난번 말씀하신 도면 수정해서 공유했습니다."))
    d = tmp_path / "meetings"
    path = save_followups(report.results, CUR, d)
    assert path.name == f"{CUR}.followups.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == list(report.results)
    with pytest.raises(FileExistsError):  # 덮어쓰기 금지
        save_followups(report.results, CUR, d)
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert not list(d.glob("*.tmp"))


def test_save_rejects_invalid_or_mismatched_results(tmp_path):
    bad = {"meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료", "confidence": "상",
           "evidence": [], "flags_to_add": [], "reason": "r"}
    with pytest.raises((SchemaValidationError, CompletionGuardError)):
        save_followups([bad], CUR, tmp_path)
    ok = {"meeting_id": PREV, "action_id": "A-001", "proposed_status": "확인 필요", "confidence": "하",
          "evidence": [], "flags_to_add": [], "reason": "r"}
    with pytest.raises(FollowupValidationError):
        save_followups([ok], CUR, tmp_path)  # meeting_id 불일치
    assert not list(tmp_path.glob("*.followups.json"))


def test_check_with_meetings_dir_saves_only_validated(store, tmp_path):
    report = _run(store, _tr("도면 건은 처리했습니다."), meetings_dir=tmp_path)
    assert report.saved_path and report.saved_path.exists()
    assert json.loads(report.saved_path.read_text(encoding="utf-8")) == list(report.results)
