"""T-03-10: 최초 생성 status 결정 TEST (SPEC 5.4/5.5) — 신규 / 확인 필요 두 값만.

경량화: 핵심 케이스만 검증한다. 이행점검 후 재판정(CHK020)은 T-09-2 범위.
"""
from meeting_action_agent.rules.status import decide_initial_status


def _s(assignee="김대리", due="11/20", flags=(), reviewer_passed=True):
    return decide_initial_status(
        assignee=assignee, due_date=due, flags=list(flags), reviewer_passed=reviewer_passed
    )


def test_all_clear_is_new():
    assert _s() == "신규"


def test_assignee_needs_confirmation():
    assert _s(assignee="확인 필요") == "확인 필요"


def test_due_needs_confirmation():
    assert _s(due="확인 필요") == "확인 필요"


def test_due_pending_alone_is_new():
    assert _s(due="미정") == "신규"


def test_due_pending_with_other_reason_still_needs_confirmation():
    assert _s(due="미정", flags=["조건부"]) == "확인 필요"
    assert _s(due="미정", assignee="확인 필요") == "확인 필요"


def test_flags_and_reviewer_fail_need_confirmation():
    assert _s(flags=["조건부"]) == "확인 필요"
    assert _s(flags=["지시여부애매"]) == "확인 필요"
    assert _s(reviewer_passed=False) == "확인 필요"


def test_other_flags_do_not_force_confirmation():
    # 팀지칭·공동·참석자외는 status 사유가 아니다 (SPEC 5.4: 플래그 표시만)
    assert _s(flags=["팀지칭", "공동", "참석자외"]) == "신규"
