"""담당자 값 규칙 (T-03-4, SPEC 5.4) — 최소 구현.

인터페이스(이름·시그니처·반환 타입)만 정의한다. 규칙 로직은 아직 구현하지 않는다.
골격은 항상 "미구현" 결과(빈 assignee)를 돌려주므로 규칙 TEST는 기능 미구현 때문에 FAIL(RED)이 된다.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

NEEDS_CONFIRMATION = "확인 필요"  # common.schema.json 의 NeedsConfirmation 과 동일


class AssigneeBasis(str, Enum):
    """담당자 값을 그렇게 결정한 이유 (감사·디버깅용)."""

    EXPLICIT = "explicit"                      # 발언에 명시된 이름·직책
    SELF_ASSIGNED = "self_assigned"            # 자기 할당 + speaker_map 실명
    SELF_UNMAPPED = "self_unmapped"            # 자기 할당이지만 speaker_map 없음
    HOMONYM = "homonym"                        # 참석자 명단 대조 시 복수 후보
    PRONOUN_OR_OMITTED = "pronoun_or_omitted"  # 대명사·담당자 생략
    NOT_IN_UTTERANCE = "not_in_utterance"      # 후보가 발언에 없음 (허위 담당자 방지)
    NOT_IMPLEMENTED = "not_implemented"        # 골격 단계 전용


@dataclass(frozen=True)
class AssigneeDecision:
    assignee: str  # 발언 표기 / 화자 실명 / NEEDS_CONFIRMATION -> ActionCandidate.assignee 에 그대로 사용
    basis: AssigneeBasis

    @property
    def needs_confirmation(self) -> bool:
        return self.assignee == NEEDS_CONFIRMATION


# 닫힌 목록 (MVP). 확장은 별도 Task.
_PRONOUNS = frozenset({"그분", "그사람", "저쪽"})
_TITLES = frozenset({
    "사원", "주임", "대리", "과장", "차장", "부장", "팀장", "실장", "본부장",
    "이사", "상무", "전무", "부사장", "사장", "대표", "팀원", "매니저",
})
_SELF_ASSIGNMENTS = tuple(
    _p.replace(" ", "") for _p in (
        "제가 하겠습니다", "제가 할게요", "제가 맡겠습니다", "제가 맡을게요",
        "저는 하겠습니다", "내가 할게",
    )
)
_HONORIFIC_RE = re.compile(r"\s*(씨|님)$")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _is_self_assignment(text: str) -> bool:
    squashed = _squash(text)
    return any(p in squashed for p in _SELF_ASSIGNMENTS)


def _strip_honorific(mention: str) -> str:
    """끝의 호칭(씨·님) 한 번만 제거. 이름이 비게 되면 그대로 둔다."""
    m = mention.strip()
    stripped = _HONORIFIC_RE.sub("", m)
    return stripped if stripped else m


def decide_assignee(
    *,
    utterance_text: str,
    speaker: str,
    mention: Optional[str] = None,
    metadata: Optional[Mapping] = None,
) -> AssigneeDecision:
    """한 발언에서 담당자 값을 결정하는 순수 함수.

    utterance_text: 근거 발언 원문 (담당자가 이 안에 있는지 대조하는 기준)
    speaker:        발언 화자 라벨 (speaker_map 의 키)
    mention:        추출기가 지목한 담당자 표기 (None 이면 담당자 생략). 발언에 없으면 채택하지 않는다.
    metadata:       meeting-metadata 계약 dict (participants, speaker_map). 없어도 동작한다.
    """
    metadata = metadata or {}

    # 1) 명백한 자기 할당 (타인 이름이 함께 있어도 자기 할당 우선)
    if _is_self_assignment(utterance_text):
        speaker_map = metadata.get("speaker_map") or {}
        entry = speaker_map.get(speaker)
        name = entry.get("name") if isinstance(entry, Mapping) else None
        if name:
            return AssigneeDecision(name, AssigneeBasis.SELF_ASSIGNED)
        return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.SELF_UNMAPPED)

    # 2) 담당자 생략
    if mention is None or not mention.strip():
        return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.PRONOUN_OR_OMITTED)

    name = _strip_honorific(mention)

    # 3) 대명사 (닫힌 목록)
    if _squash(name) in _PRONOUNS:
        return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.PRONOUN_OR_OMITTED)

    # 4) 발언에 없는 이름은 채택하지 않는다
    if _squash(name) not in _squash(utterance_text):  # 공백 차이("박 대리님")만 허용, 이름 자체는 반드시 발언에 있어야 함
        return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.NOT_IN_UTTERANCE)

    # 5) 직책만 언급 -> 개인 특정 불가
    if _squash(name) in _TITLES:
        return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.PRONOUN_OR_OMITTED)

    # 6) 참석자 대조: 정확 일치 우선, 부분 일치 후보가 복수면 확인 필요
    participants = list(metadata.get("participants") or [])
    if name not in participants:
        partial = [p for p in participants if name in p]
        if len(partial) >= 2:
            return AssigneeDecision(NEEDS_CONFIRMATION, AssigneeBasis.HOMONYM)

    return AssigneeDecision(name, AssigneeBasis.EXPLICIT)


# ---------------------------------------------------------------------------
# T-03-11: 담당자 관련 플래그 (팀지칭·공동·참석자외)
# ---------------------------------------------------------------------------
_TEAM_SUFFIXES = ("팀", "부", "실")  # MVP: 명시적 접미사만. fuzzy/LLM/조직명 추측 금지.


@dataclass(frozen=True)
class AssigneeFlags:
    assignee: str
    flags: tuple  # contract Flag 값 (팀지칭 / 공동 / 참석자외)


@dataclass(frozen=True)
class AssigneeFlagResult:
    """담당자 플래그 판정 결과. 실제 Action 분리는 T-03-12에서 per_assignee 를 사용해 수행한다."""

    per_assignee: tuple  # tuple[AssigneeFlags, ...] — 입력 mentions 순서
    flag_notes: dict     # contract flag_notes 형식 {Flag: {"note": str}}

    @property
    def is_joint(self) -> bool:
        return len(self.per_assignee) >= 2


def is_team_reference(name: str) -> bool:
    """명시적 접미사(~팀/~부/~실)만 팀·부서로 인정한다."""
    n = name.strip()
    return len(n) >= 2 and n.endswith(_TEAM_SUFFIXES)


def decide_assignee_flags(
    *,
    utterance_text: str,
    speaker: str,
    mentions: list,
    metadata: Optional[Mapping] = None,
) -> AssigneeFlagResult:
    """담당자 표기 목록에 대해 팀지칭·공동·참석자외 플래그를 판정한다 (순수 함수).

    담당자 값 자체는 decide_assignee(T-03-4)를 재사용한다.
    참석자외는 participants 가 실제 제공된 경우에만 판정한다.
    """
    metadata = metadata or {}
    participants = list(metadata.get("participants") or [])
    joint = len(mentions) >= 2

    items = []
    for mention in mentions:
        d = decide_assignee(
            utterance_text=utterance_text, speaker=speaker, mention=mention, metadata=metadata
        )
        flags = []
        if not d.needs_confirmation:
            if is_team_reference(d.assignee):
                flags.append("팀지칭")
            elif participants and d.assignee not in participants:
                flags.append("참석자외")
        if joint:
            flags.append("공동")
        items.append(AssigneeFlags(d.assignee, tuple(flags)))

    notes = {}
    if joint:
        notes["공동"] = {"note": "공동 담당: " + ", ".join(i.assignee for i in items)}
    return AssigneeFlagResult(tuple(items), notes)
