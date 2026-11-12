"""T-03-9: 기한 값 규칙·환산 TEST (SPEC 5.4, CHK019 결정).

경량화: 대표 수용기준만 검증. 회의일 2026-11-10(화) 기준.
핵심 원칙: 코드가 규칙으로만 환산하고, 규칙에 없으면 null. 원문은 항상 보존.
범위 밖: 다음 해 추정, N일 후, 공휴일·영업일, 다음 회의일 연동.
"""
import pytest

from meeting_action_agent.rules.due_date import (
    NEEDS_CONFIRMATION,
    PENDING,
    decide_due_date,
)

MEETING = "2026-11-10"  # 화요일


def _d(raw):
    return decide_due_date(raw=raw, meeting_date=MEETING)


def test_weekday_alone_is_first_such_weekday_after_meeting_and_raw_preserved():
    r = _d("목요일 오전까지")
    assert (r.due_date, r.due_date_normalized) == ("목요일 오전까지", "2026-11-12")
    assert _d("월요일").due_date_normalized == "2026-11-16"  # 회의일 이후 첫 월요일


def test_same_weekday_as_meeting_is_not_normalized():
    r = _d("화요일까지")
    assert (r.due_date, r.due_date_normalized) == ("화요일까지", None)


@pytest.mark.parametrize("raw, expected", [
    ("11/12", "2026-11-12"),
    ("11월 12일까지", "2026-11-12"),
    ("11/10", "2026-11-10"),   # 회의일 당일은 과거가 아님
    ("11/5", None),            # 회의일보다 과거 -> 다음 해로 이월하지 않음
    ("1/5", None),             # 연말/연초 추론 없음
    ("2/30", None),            # 존재하지 않는 날짜
])
def test_month_day_uses_meeting_year_and_never_rolls_over(raw, expected):
    r = _d(raw)
    assert r.due_date == raw
    assert r.due_date_normalized == expected


@pytest.mark.parametrize("raw", [
    "다음 주 초", "이번 주 내",                  # 범위 표현
    "다음 회의까지",                              # 다음 회의일 미확정
    "3일 후",                                     # MVP 범위 밖: 규칙에 없는 표현
])
def test_week_qualified_range_and_unlisted_expressions_are_null_with_raw_kept(raw):
    r = _d(raw)
    assert (r.due_date, r.due_date_normalized) == (raw, None)


@pytest.mark.parametrize("raw, expected", [
    ("미정", PENDING),                  # 명시적 미정
    (None, NEEDS_CONFIRMATION),         # 언급 없음
    ("  ", NEEDS_CONFIRMATION),
    ("확인 필요", NEEDS_CONFIRMATION),
    ("조만간", NEEDS_CONFIRMATION),     # 해석 불가 표현 (닫힌 목록)
    ("빨리", NEEDS_CONFIRMATION),
])
def test_pending_vs_needs_confirmation_always_null_normalized(raw, expected):
    r = _d(raw)
    assert r.due_date == expected
    assert r.due_date_normalized is None
