"""Step B: '이번주/다음주 + 요일' 환산. 주 기준 월~일. meeting_date 기준, Python date 연산."""
import pytest

from meeting_action_agent.rules.due_date import decide_due_date


def _d(raw, meeting="2026-11-17"):  # 화요일
    return decide_due_date(raw=raw, meeting_date=meeting)


@pytest.mark.parametrize("raw, expected", [
    ("다음주 월요일", "2026-11-23"),
    ("다음주 화요일", "2026-11-24"),
    ("다음주 화요일까지", "2026-11-24"),
    ("다음 주 화요일까지", "2026-11-24"),
    ("다음주 일요일", "2026-11-29"),
    ("이번주 화요일", "2026-11-17"),   # 회의일 당일: 문자 그대로
    ("이번주 금요일", "2026-11-20"),
    ("이번 주 월요일", "2026-11-16"),  # 이미 지난 요일도 다음주로 넘기지 않음
    ("이번주 일요일까지", "2026-11-22"),
])
def test_week_qualified_weekday(raw, expected):
    r = _d(raw)
    assert r.due_date == raw  # 원문 유지
    assert r.due_date_normalized == expected


def test_year_boundary():
    assert _d("다음주 화요일", "2026-12-29").due_date_normalized == "2027-01-05"


def test_plain_weekday_policy_unchanged():
    assert _d("금요일").due_date_normalized == "2026-11-20"
    assert _d("화요일까지").due_date_normalized is None  # 회의일과 같은 요일


@pytest.mark.parametrize("raw", ["다음주 초", "다음주쯤", "이번주 내", "다음주", "다다음주 화요일"])
def test_unsupported_ranges_stay_null(raw):
    r = _d(raw)
    assert (r.due_date, r.due_date_normalized) == (raw, None)
