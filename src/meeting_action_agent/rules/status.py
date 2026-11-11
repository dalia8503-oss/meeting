"""Action 최초 생성 시점 status 결정 규칙 (T-03-10, SPEC 5.4/5.5).

결과는 `신규` / `확인 필요` 두 값뿐이다.
이행점검 후 status 재판정(진행중·완료 후보·완료)은 범위 밖이다 (CHK020 → T-09-2).
"""
from typing import Iterable

NEW = "신규"
NEEDS_CONFIRMATION = "확인 필요"  # common.schema.json 의 NeedsConfirmation / Status 값과 동일
PENDING = "미정"

# status 를 `확인 필요`로 만드는 flag (닫힌 목록). 팀지칭·공동·참석자외 등은 표시만 하고 status에 영향 없음.
_CONFIRMATION_FLAGS = frozenset({"조건부", "지시여부애매"})


def decide_initial_status(
    *,
    assignee: str,
    due_date: str,
    flags: Iterable[str] = (),
    reviewer_passed: bool = True,
) -> str:
    """최초 생성 시점의 status 를 결정하는 순수 함수. 기한 `미정`은 그 자체로는 사유가 아니다."""
    if assignee == NEEDS_CONFIRMATION:
        return NEEDS_CONFIRMATION
    if due_date == NEEDS_CONFIRMATION:
        return NEEDS_CONFIRMATION
    if _CONFIRMATION_FLAGS.intersection(flags):
        return NEEDS_CONFIRMATION
    if not reviewer_passed:
        return NEEDS_CONFIRMATION
    return NEW
