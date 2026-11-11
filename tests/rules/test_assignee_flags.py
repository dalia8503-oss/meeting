"""T-03-11: 담당자 관련 플래그 TEST (SPEC 5.4) — 팀지칭·공동·참석자외.

경량화: 핵심 케이스만 검증한다. 복수 담당자의 Action 분리는 T-03-12 범위.
"""
import pytest

from meeting_action_agent.rules.assignee import decide_assignee_flags

META = {"participants": ["김대리", "박과장", "민수"]}


def _flags(text, mentions, metadata=None):
    return decide_assignee_flags(
        utterance_text=text, speaker="SPEAKER_00", mentions=mentions, metadata=metadata
    )


@pytest.mark.parametrize(
    "text,mention",
    [
        ("생산팀에서 확인해 주세요.", "생산팀"),
        ("공사1부에서 처리해 주세요.", "공사1부"),
        ("기술지원실에서 점검해 주세요.", "기술지원실"),
    ],
)
def test_team_suffix_gets_team_flag(text, mention):
    r = _flags(text, [mention], META)
    assert r.per_assignee[0].assignee == mention
    assert r.per_assignee[0].flags == ("팀지칭",)
    assert "참석자외" not in r.per_assignee[0].flags  # 팀은 명단 대조 대상 아님


def test_multiple_assignees_get_joint_flag_and_note():
    r = _flags("김대리, 박과장이 같이 처리해 주세요.", ["김대리", "박과장"], META)
    assert r.is_joint
    assert [a.assignee for a in r.per_assignee] == ["김대리", "박과장"]
    assert all("공동" in a.flags for a in r.per_assignee)
    assert "공동" in r.flag_notes and r.flag_notes["공동"]["note"]


def test_outside_participants_flagged_only_when_list_given():
    out = _flags("철수 씨가 처리해 주세요.", ["철수 씨"], META)
    assert out.per_assignee[0].flags == ("참석자외",)

    for md in (None, {}, {"participants": []}):
        r = _flags("철수 씨가 처리해 주세요.", ["철수 씨"], md)
        assert r.per_assignee[0].flags == ()


def test_single_normal_assignee_has_no_flags():
    r = _flags("민수 씨가 처리해 주세요.", ["민수 씨"], META)
    assert not r.is_joint
    assert r.per_assignee[0].assignee == "민수"
    assert r.per_assignee[0].flags == ()
    assert r.flag_notes == {}
