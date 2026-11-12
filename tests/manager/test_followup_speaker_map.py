"""T-LIVE-SPK: 사용자 확인 speaker_map과 Follow-up/Reviewer 안전 규칙. 외부 LLM 호출 없음 (fake runner).

핵심: LIVE STT의 익명 라벨(speaker_N)만으로는 담당자 본인을 증명할 수 없다. 사람이 확인한 매핑이 있을 때만 완료 후보가 가능하다.
"""
import copy
import json

import pytest

from meeting_action_agent.manager.followup import build_prompt, check_previous_actions
from meeting_action_agent.manager.followup_apply import prepare_followup_with_retry
from meeting_action_agent.reviewer import build_followup_prompt, code_check_followup
from meeting_action_agent.store import ActionStore

PREV, CUR = "20261110-01", "20261117-01"
DONE = "수정 완료해서 어제 공유했습니다."
MAP_KIM = {"speaker_2": {"name": "김대리"}}
TRANSCRIPT = [
    {"meeting_id": CUR, "speaker": "speaker_1", "timestamp": "00:00:05", "text": "김대리님, 지난번 도면 수정은 어떻게 됐어요?"},
    {"meeting_id": CUR, "speaker": "speaker_2", "timestamp": "00:00:10", "text": DONE},
]


@pytest.fixture
def store(store_path, new_action):
    s = ActionStore(store_path)
    a = copy.deepcopy(new_action)
    a.update(action_id="A-001", meeting_id=PREV, assignee="김대리", task="도면 수정해서 공유",
             due_date="금요일", due_date_normalized=None, status="신규")
    s.create(a)
    return s


def _done_runner(prompts=None):
    def runner(prompt):
        if prompts is not None:
            prompts.append(prompt)
        u = TRANSCRIPT[1]
        return json.dumps({"meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료 후보", "confidence": "상",
                           "evidence": [{"meeting_id": CUR, "timestamp": u["timestamp"], "speaker": u["speaker"],
                                         "text": u["text"]}], "flags_to_add": [], "reason": "완료형 발언"},
                          ensure_ascii=False)
    return runner


def _check(store, speaker_map, prompts=None):
    return check_previous_actions(TRANSCRIPT, _done_runner(prompts), store=store, speaker_map=speaker_map)


# 9. mapping 없음 + 이름 없는 완료 발언 → 완료 후보 금지 (fail-closed)
def test_no_speaker_map_unnamed_completion_rejected(store):
    r = _check(store, None)
    assert r.results == () and len(r.rejected) == 1 and "본인" in r.rejected[0]["reason"]


# 10. 올바른 mapping + 완료 발언 → 완료 후보 가능
def test_matching_speaker_map_allows_completion_candidate(store):
    r = _check(store, MAP_KIM)
    assert len(r.results) == 1 and r.results[0]["proposed_status"] == "완료 후보"


def test_plain_string_map_form_is_accepted(store):
    assert len(_check(store, {"speaker_2": "김대리"}).results) == 1


# 11. 잘못된 사람 mapping → 완료 후보 금지
def test_mapping_to_other_person_rejected(store):
    r = _check(store, {"speaker_2": {"name": "박대리"}})
    assert r.results == () and len(r.rejected) == 1


# 12. 미확인 speaker → 완료 후보 금지 (다른 화자만 확인된 경우 포함)
def test_unconfirmed_speaker_rejected(store):
    assert _check(store, {"speaker_1": {"name": "팀장"}}).results == ()
    assert _check(store, {"speaker_2": {"name": "  "}}).results == ()  # 빈 이름 = 미확인


# 담당자 이름이 발언에 직접 있으면 기존대로 허용 (기존 evidence 규칙 약화·강화 없음)
def test_assignee_name_in_text_still_allowed_without_map(store):
    text = "김대리가 도면 수정해서 공유했습니다."
    tr = [{"meeting_id": CUR, "speaker": "speaker_3", "timestamp": "00:00:10", "text": text}]

    def runner(prompt):
        return json.dumps({"meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료 후보", "confidence": "상",
                           "evidence": [{"meeting_id": CUR, "timestamp": "00:00:10", "speaker": "speaker_3", "text": text}],
                           "flags_to_add": [], "reason": "r"}, ensure_ascii=False)
    assert len(check_previous_actions(tr, runner, store=store).results) == 1


# 확인 필요(근거 없음)는 mapping과 무관하게 항상 가능하다
def test_no_evidence_result_unaffected_by_map(store):
    def runner(prompt):
        return json.dumps({"meeting_id": CUR, "action_id": "A-001", "proposed_status": "확인 필요", "confidence": "하",
                           "evidence": [], "flags_to_add": [], "reason": "언급 없음"}, ensure_ascii=False)
    assert len(check_previous_actions(TRANSCRIPT, runner, store=store).results) == 1


# 14. 근거는 원본 speaker_N 그대로 (이름으로 교체하지 않음)
def test_evidence_keeps_original_speaker_label(store):
    r = _check(store, MAP_KIM)
    assert r.results[0]["evidence"][0]["speaker"] == "speaker_2"


# 14-b. prompt에는 사용자 확인 화자만 들어가고, 없으면 블록 자체가 없다 (Agent가 이름을 추측하지 않도록)
def test_followup_prompt_carries_only_confirmed_map(store):
    with_map, without = [], []
    _check(store, MAP_KIM, with_map)
    _check(store, None, without)
    assert "사용자 확인 화자" in with_map[0] and '"speaker_2": "김대리"' in with_map[0]
    assert "사용자 확인 화자" not in without[0]
    assert build_prompt(store.get("A-001"), TRANSCRIPT, speaker_map={"speaker_2": {"name": "김대리"}}).count("김대리") >= 2


# 13. Reviewer도 같은 사용자 확인 매핑을 검증 context로 받는다 (스스로 추론하지 않는다)
def test_reviewer_receives_and_uses_speaker_map(store):
    action = store.get("A-001")
    u = TRANSCRIPT[1]
    result = {"meeting_id": CUR, "action_id": "A-001", "proposed_status": "완료 후보", "confidence": "상",
              "evidence": [{"meeting_id": CUR, "timestamp": u["timestamp"], "speaker": "speaker_2", "text": u["text"]}],
              "flags_to_add": [], "reason": "r"}
    assert code_check_followup(action, TRANSCRIPT, result, MAP_KIM) == []
    assert code_check_followup(action, TRANSCRIPT, result, None)[0]["code"] == "판정비도출"  # 매핑 없으면 REJECT
    assert "사용자 확인 화자" in build_followup_prompt(action, TRANSCRIPT, result, speaker_map=MAP_KIM)
    assert "사용자 확인 화자" not in build_followup_prompt(action, TRANSCRIPT, result)


def test_prepare_followup_passes_map_to_reviewer_prompt(store):
    seen = []

    def reviewer(prompt):
        seen.append(prompt)
        return {"target": {"type": "followup", "ref": "A-001"}, "verdict": "PASS", "reasons": [], "attempt": 0}
    first = json.loads(_done_runner()(""))
    prepared = prepare_followup_with_retry(store, first, TRANSCRIPT, _done_runner(), reviewer, MAP_KIM)
    assert prepared.final is None and prepared.result["proposed_status"] == "완료 후보"
    assert "사용자 확인 화자" in seen[0]
    # 같은 결과도 매핑 없이는 Reviewer 이전 코드 검증에서 막힌다
    with pytest.raises(Exception):
        prepare_followup_with_retry(store, first, TRANSCRIPT, _done_runner(), reviewer, None)


# 데모 E2E에서 발견: STT 담당자 "김 대리"(띄어쓰기) vs 사용자가 입력한 화자 이름 "김대리" → 같은 이름으로 본다.
def test_whitespace_only_difference_is_same_person(store_path, new_action):
    s = ActionStore(store_path)
    a = copy.deepcopy(new_action)
    a.update(action_id="A-001", meeting_id=PREV, assignee="김 대리", task="도면 수정해서 공유",
             due_date="금요일", due_date_normalized=None, status="신규")
    s.create(a)
    r = _check(s, MAP_KIM)
    assert len(r.results) == 1 and r.results[0]["proposed_status"] == "완료 후보"


# 글자가 다르면 띄어쓰기를 무시해도 다른 사람이다 (fail-closed 유지).
def test_different_name_still_rejected(store):
    r = _check(store, {"speaker_2": {"name": "박대리"}})
    assert r.results == () and len(r.rejected) == 1 and "본인" in r.rejected[0]["reason"]
