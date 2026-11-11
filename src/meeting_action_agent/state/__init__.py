"""상태 전이·완료 가드 (T-03-2, T-03-3).

골격(skeleton) 단계: 테스트가 import·수집될 수 있도록 이름만 정의한다.
전이표 로직은 아직 구현하지 않는다.
"""
from enum import Enum


class Status(str, Enum):
    """SPEC 5.3 상태값 (common.schema.json의 Status와 동일). 저장 값에 이모지는 쓰지 않는다."""

    NEW = "신규"
    IN_PROGRESS = "진행중"
    DONE_CANDIDATE = "완료 후보"
    DONE = "완료"
    NOT_DONE = "미완료"
    NEEDS_CHECK = "확인 필요"


class Actor(str, Enum):
    """상태 변경 주체 (common.schema.json의 Actor)."""

    AGENT = "Agent"
    HUMAN = "사람"


class TransitionError(Exception):
    """전이표에 없는 전이."""


class CompletionGuardError(Exception):
    """완료 가드 위반 (승인값 없음 등)."""


_AGENT_SOURCES = frozenset(
    {Status.NEW, Status.IN_PROGRESS, Status.NEEDS_CHECK, Status.NOT_DONE}
)
_AGENT_TARGETS = frozenset(
    {Status.IN_PROGRESS, Status.NOT_DONE, Status.NEEDS_CHECK, Status.DONE_CANDIDATE}
)

# SPEC 5.6 상태 전이표: (출발 상태, 도착 상태, 주체). 출발 None은 최초 생성.
# 표에 없는 조합은 모두 허용하지 않는다 (자기 전이는 전이가 아니므로 항상 거부).
ALLOWED_TRANSITIONS = frozenset(
    {
        # (없음) → 신규 / 확인 필요 : Agent
        (None, Status.NEW, Actor.AGENT),
        (None, Status.NEEDS_CHECK, Actor.AGENT),
        # 신규·진행중·확인 필요·미완료 → 진행중 / 미완료 / 확인 필요 / 완료 후보 : Agent
        *(
            (src, dst, Actor.AGENT)
            for src in _AGENT_SOURCES
            for dst in _AGENT_TARGETS
            if src != dst  # 현재와 다른 상태로만 전이. 자기 전이는 상태 전이가 아니다 (SPEC 5.6)
        ),
        # 완료 후보 → 완료 / 진행중 / 확인 필요 : 사람
        (Status.DONE_CANDIDATE, Status.DONE, Actor.HUMAN),
        (Status.DONE_CANDIDATE, Status.IN_PROGRESS, Actor.HUMAN),
        (Status.DONE_CANDIDATE, Status.NEEDS_CHECK, Actor.HUMAN),
        # 완료 → 진행중 (완료 철회) : 사람
        (Status.DONE, Status.IN_PROGRESS, Actor.HUMAN),
    }
)


def is_allowed(from_status, to_status, actor) -> bool:
    """SPEC 5.6 전이표에 있는 전이만 True. 그 외(Agent의 '완료' 포함)는 False."""
    try:
        key = (
            None if from_status is None else Status(from_status),
            Status(to_status),
            Actor(actor),
        )
    except ValueError:
        return False
    return key in ALLOWED_TRANSITIONS
