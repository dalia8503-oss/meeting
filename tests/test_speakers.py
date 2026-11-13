"""T-LIVE-SPK 순수 함수: 화자 목록·대표 발언·speaker_map 검증."""
import pytest

from meeting_action_agent.speakers import build_profile as P

from meeting_action_agent.speakers import (
    SpeakerMapError, confirmed_names, list_speakers, speaker_context_text, validate_speaker_map)

TR = [
    {"meeting_id": "m", "speaker": "speaker_2", "timestamp": "00:00:10", "text": "수정 완료해서 어제 공유했습니다."},
    {"meeting_id": "m", "speaker": "speaker_1", "timestamp": "00:00:05", "text": "김대리님, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?"},
    {"meeting_id": "m", "speaker": "speaker_2", "timestamp": "00:00:20", "text": "네."},
    {"meeting_id": "m", "speaker": "speaker_2", "timestamp": "00:00:30", "text": "네, 확인했습니다."},
    {"meeting_id": "m", "speaker": "speaker_2", "timestamp": "00:00:40", "text": "다음 주에 같이 보겠습니다 그때 말씀드릴게요."},
    {"meeting_id": "m", "speaker": "speaker_10", "timestamp": "00:00:50", "text": "안녕하세요."},
]


# 1. speaker 목록 추출 (번호순, 숫자 정렬)
def test_list_speakers():
    out = list_speakers(TR)
    assert [s["speaker"] for s in out] == ["speaker_1", "speaker_2", "speaker_10"]
    assert [s["utterance_count"] for s in out] == [1, 4, 1]


# 2. 대표 발언은 실제 transcript 원문이다 (최대 3개, 시간순). 생성·요약 없음
def test_samples_are_real_utterances():
    texts = {u["text"] for u in TR}
    for s in list_speakers(TR):
        assert len(s["samples"]) <= 3
        assert all(x["text"] in texts for x in s["samples"])
        assert [x["timestamp"] for x in s["samples"]] == sorted(x["timestamp"] for x in s["samples"])
    s2 = list_speakers(TR)[1]
    assert "수정 완료해서 어제 공유했습니다." in [x["text"] for x in s2["samples"]]
    assert "네." not in [x["text"] for x in s2["samples"]]  # 가장 짧은 발언은 대표에서 밀린다


def test_list_speakers_does_not_modify_transcript():
    import copy
    before = copy.deepcopy(TR)
    list_speakers(TR)
    assert TR == before


# 3~5. 검증: 유효 저장 형태 / 존재하지 않는 speaker 거부 / 빈 이름 거부 / null=미확인
def test_validate_ok_and_unconfirmed():
    assert validate_speaker_map({"speaker_1": " 팀장 ", "speaker_2": None}, TR) == {"speaker_1": P("팀장")}
    assert validate_speaker_map({}, TR) == {}


def test_validate_unknown_speaker_rejected():
    with pytest.raises(SpeakerMapError):
        validate_speaker_map({"speaker_99": "팀장"}, TR)


@pytest.mark.parametrize("bad", ["", "   ", 3])
def test_validate_empty_name_rejected(bad):
    with pytest.raises(SpeakerMapError):
        validate_speaker_map({"speaker_1": bad}, TR)


def test_same_name_for_several_speakers_allowed():
    assert validate_speaker_map({"speaker_1": "김대리", "speaker_2": "김대리"}, TR) == {
        "speaker_1": P("김대리"), "speaker_2": P("김대리")}


def test_confirmed_names_forms():
    assert confirmed_names({"speaker_1": {"name": "팀장"}, "speaker_2": "김대리", "speaker_3": {"name": " "},
                            "speaker_4": None}) == {"speaker_1": "팀장", "speaker_2": "김대리"}
    assert confirmed_names(None) == {} and speaker_context_text(None) == "" and speaker_context_text({}) == ""


# ---- 조직정보(부/과) 구조화 프로필
def test_build_profile_structured_and_display_name():
    p = P("이과장")
    assert p == {"name": "이과장", "display_name": "이과장", "confirmed_by_human": True}
    from meeting_action_agent.speakers import build_profile
    q = build_profile("이과장", " 1부 ", "3과")
    assert q == {"name": "이과장", "department": "1부", "section": "3과", "display_name": "1부 3과 이과장",
                 "confirmed_by_human": True}
    assert build_profile("이과장", None, "3과")["display_name"] == "3과 이과장"


def test_validate_accepts_object_and_rejects_org_without_name():
    out = validate_speaker_map({"speaker_1": {"name": "팀장", "department": "1부", "section": ""}}, TR)
    assert out["speaker_1"]["display_name"] == "1부 팀장" and "section" not in out["speaker_1"]
    with pytest.raises(SpeakerMapError):
        validate_speaker_map({"speaker_1": {"name": " ", "department": "1부"}}, TR)


def test_confirmed_names_ignores_org_and_legacy_profiles_still_work():
    from meeting_action_agent.speakers import confirmed_profiles, display_for_person
    m = {"speaker_1": {"name": "이과장", "department": "1부", "section": "3과"}, "speaker_2": {"name": "김대리"}}
    assert confirmed_names(m) == {"speaker_1": "이과장", "speaker_2": "김대리"}  # identity는 name만
    prof = confirmed_profiles(m)
    assert prof["speaker_1"]["display_name"] == "1부 3과 이과장" and prof["speaker_2"]["display_name"] == "김대리"
    assert display_for_person("이 과장", prof) == "1부 3과 이과장"
    assert display_for_person("박대리", prof) is None  # 확인되지 않은 이름은 추측하지 않는다
    dup = {"speaker_1": {"name": "이과장", "department": "1부"}, "speaker_2": {"name": "이과장", "department": "2부"}}
    assert display_for_person("이과장", confirmed_profiles(dup)) is None  # 모호하면 표시하지 않는다
