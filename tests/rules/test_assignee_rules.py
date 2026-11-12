"""T-03-4 (B): 담당자 값 규칙 TEST (SPEC 5.4).

경량화(캡스톤 MVP): 대표 수용기준만 검증한다. 표현 변형은 테스트하지 않는다.
핵심 원칙: 발언에 없는 담당자를 만들어내지 않는다.

범위 밖(T-03-11): 팀지칭·공동·참석자외 플래그. 범위 밖(후속 Task): 여러 발언 결합.
"""
import copy
import json
from pathlib import Path

import pytest

from meeting_action_agent.contracts import validate
from meeting_action_agent.rules.assignee import (
    NEEDS_CONFIRMATION,
    AssigneeBasis,
    decide_assignee,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "specs" / "001-meeting-action-agent" / "contracts"
EXAMPLES = CONTRACTS / "examples" / "valid"


def _ex(name):
    return copy.deepcopy(json.loads((EXAMPLES / name).read_text(encoding="utf-8")))


@pytest.fixture
def metadata():
    """contracts valid 예시(meeting-metadata.json) + 화자 매핑 1건 추가. 참석자: 팀장·민수·김대리·박과장."""
    m = _ex("meeting-metadata.json")
    m["speaker_map"]["SPEAKER_01"] = {"name": "박과장", "position": "과장"}
    validate("meeting-metadata", m)
    return m


@pytest.fixture
def metadata_without_speaker_map(metadata):
    m = copy.deepcopy(metadata)
    del m["speaker_map"]
    validate("meeting-metadata", m)
    return m


def _decide(text, mention, metadata=None, speaker="SPEAKER_00"):
    return decide_assignee(utterance_text=text, speaker=speaker, mention=mention, metadata=metadata)


def _with_participants(metadata, participants):
    m = copy.deepcopy(metadata)
    m["participants"] = participants
    return m


# 1. 명시된 이름은 발언 표기 그대로, 호칭(씨·님)만 제거. 명단/metadata 없어도 유지, 확장 금지
@pytest.mark.parametrize(
    "text,mention,expected,use_metadata",
    [
        ("민수 씨 자료 확인해 주세요.", "민수 씨", "민수", True),
        ("김 대리님 이거 부탁합니다.", "김 대리님", "김 대리", True),  # 내부 공백 유지
        ("철수 씨가 자료 정리해 주세요.", "철수 씨", "철수", False),   # metadata 없음
    ],
)
def test_explicit_name_strips_only_honorific(text, mention, expected, use_metadata, metadata):
    d = _decide(text, mention, metadata if use_metadata else None)
    assert (d.assignee, d.basis) == (expected, AssigneeBasis.EXPLICIT)


# 2. 정확 일치 우선 / 부분 이름 + 복수 후보는 확인 필요 (이름 확장 금지)
def test_exact_match_wins_and_homonym_needs_confirmation(metadata):
    exact = _decide("민수 씨 확인해 주세요.", "민수 씨", _with_participants(metadata, ["민수", "김민수"]))
    assert (exact.assignee, exact.basis) == ("민수", AssigneeBasis.EXPLICIT)

    homonym = _decide("민수 씨 확인해 주세요.", "민수 씨", _with_participants(metadata, ["김민수", "박민수"]))
    assert (homonym.assignee, homonym.basis) == (NEEDS_CONFIRMATION, AssigneeBasis.HOMONYM)

    single = _decide("민수 씨 확인해 주세요.", "민수 씨", _with_participants(metadata, ["김민수", "박영희"]))
    assert single.assignee == "민수"  # '김민수'로 확장하지 않는다


# 3. 직책만 / 대명사 / 담당자 생략 -> 확인 필요
@pytest.mark.parametrize(
    "text,mention",
    [("대리님 이거 부탁합니다.", "대리님"), ("그분이 확인해 주세요.", "그분"), ("다음 주까지 정리해 주세요.", None)],
)
def test_title_only_pronoun_or_omitted_needs_confirmation(text, mention, metadata):
    d = _decide(text, mention, metadata)
    assert (d.assignee, d.basis) == (NEEDS_CONFIRMATION, AssigneeBasis.PRONOUN_OR_OMITTED)


# 4. 허위 담당자 방지: 발언에 없는 이름(참석자 명단에 있어도, 확장된 이름이어도)은 확정하지 않는다
@pytest.mark.parametrize(
    "text,mention",
    [("이거 확인해 주세요.", "민수"), ("민수 씨 확인해 주세요.", "김민수")],
)
def test_name_not_in_utterance_is_not_confirmed(text, mention, metadata):
    d = _decide(text, mention, metadata)
    assert (d.assignee, d.basis) == (NEEDS_CONFIRMATION, AssigneeBasis.NOT_IN_UTTERANCE)


# 5. 자기 할당: speaker_map 실명 사용, 타인 이름이 있어도 자기 할당 우선
def test_self_assignment_uses_speaker_map_name(metadata):
    d = _decide("제가 하겠습니다.", None, metadata, speaker="SPEAKER_01")
    assert (d.assignee, d.basis) == ("박과장", AssigneeBasis.SELF_ASSIGNED)
    d = _decide("민수 씨 말고 제가 할게요.", "민수 씨", metadata, speaker="SPEAKER_01")
    assert d.assignee == "박과장"


# 6. 자기 할당 + 매핑 없음 -> 확인 필요 (화자 라벨을 담당자로 쓰지 않는다)
def test_self_assignment_without_mapping_needs_confirmation(metadata_without_speaker_map):
    for text, mention in [("제가 하겠습니다.", None), ("민수 씨 말고 제가 할게요.", "민수 씨")]:
        d = _decide(text, mention, metadata_without_speaker_map, speaker="SPEAKER_01")
        assert (d.assignee, d.basis) == (NEEDS_CONFIRMATION, AssigneeBasis.SELF_UNMAPPED)


# 7. 결과가 현재 action-candidate contract 와 호환 (NOT_IMPLEMENTED 미사용 포함)
@pytest.mark.parametrize(
    "text,mention,speaker",
    [("민수 씨 확인해 주세요.", "민수 씨", "SPEAKER_00"), ("그거 봐주세요.", None, "SPEAKER_00"), ("제가 하겠습니다.", None, "SPEAKER_01")],
)
def test_result_is_compatible_with_action_candidate_contract(text, mention, speaker, metadata):
    d = _decide(text, mention, metadata, speaker=speaker)
    assert d.basis != AssigneeBasis.NOT_IMPLEMENTED
    candidate = _ex("action-candidate.json")
    candidate["assignee"] = d.assignee
    if d.needs_confirmation:
        candidate["status"] = "확인 필요"
    validate("action-candidate", candidate)


def test_needs_confirmation_constant_matches_contract_value():
    common = json.loads((CONTRACTS / "common.schema.json").read_text(encoding="utf-8"))
    assert NEEDS_CONFIRMATION == common["$defs"]["NeedsConfirmation"]["const"]


def test_assignee_matches_name_with_spacing_difference():
    """T-09-11: '박 대리님' 처럼 공백만 다른 표기는 발언에 있는 이름으로 인정한다. 없는 이름은 여전히 확인 필요."""
    ok = decide_assignee(utterance_text="이번에는 박 대리님이 검토해 주세요.", speaker="팀장", mention="박대리")
    assert ok.assignee == "박대리"
    bad = decide_assignee(utterance_text="이번에는 최 대리님이 검토해 주세요.", speaker="팀장", mention="박대리")
    assert bad.assignee == "확인 필요"
