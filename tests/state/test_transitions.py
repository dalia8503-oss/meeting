"""T-03-2: 상태 enum + 전이표 (SPEC 5.6, data-model 3장).

TEST 단계: 구현 전이므로 FAIL이 정상이다 (기능 미구현).

검증 대상
- 허용 전이는 통과, 표에 없는 전이는 거부
- Agent의 '완료' 설정 차단 (SPEC 5.3, 9장 / CLAUDE.md 절대 원칙 5)
- '완료'는 '완료 후보'에서 사람만 설정 (SPEC US-05, 5.6)

자기 전이(신규→신규 등)는 상태 전이가 아니다 (SPEC 5.6, 2026-11-11 결정). is_allowed()는
항상 False이며, 상태가 유지되는 재판정은 별도 경로(record_reassessment, T-03-3 범위)가 기록한다.
"""
import itertools

import pytest

from meeting_action_agent.state import Actor, Status, TransitionError, is_allowed

AGENT, HUMAN = Actor.AGENT, Actor.HUMAN
NEW, DOING, CAND, DONE, UNDONE, CHECK = (
    Status.NEW, Status.IN_PROGRESS, Status.DONE_CANDIDATE,
    Status.DONE, Status.NOT_DONE, Status.NEEDS_CHECK,
)

# SPEC 5.6 상태 전이표를 그대로 옮긴 기대값 (from, to, actor). from=None은 최초 생성.
EXPECTED_ALLOWED = {
    # (없음) → 신규 / 확인 필요 : Agent
    (None, NEW, AGENT), (None, CHECK, AGENT),
    # 신규·진행중·확인 필요·미완료 → 진행중/미완료/확인 필요/완료 후보 : Agent
    *[(f, t, AGENT)
      for f in (NEW, DOING, CHECK, UNDONE)
      for t in (DOING, UNDONE, CHECK, CAND) if f != t],  # '현재와 다른' 상태로만
    # 완료 후보 → 완료 / 진행중 / 확인 필요 : 사람
    (CAND, DONE, HUMAN), (CAND, DOING, HUMAN), (CAND, CHECK, HUMAN),
    # 완료 → 진행중 (완료 철회) : 사람
    (DONE, DOING, HUMAN),
}

# 자기 전이(f == t)도 포함한다: 표에 없으므로 거부되어야 한다.
ALL_CASES = list(itertools.product([None, *Status], list(Status), list(Actor)))


def _id(v):
    return str(getattr(v, "value", v))


def test_status_enum_matches_spec_5_3():
    assert {s.value for s in Status} == {
        "신규", "진행중", "완료 후보", "완료", "미완료", "확인 필요"
    }


@pytest.mark.parametrize("frm,to,actor", sorted(EXPECTED_ALLOWED, key=str), ids=_id)
def test_allowed_transitions_pass(frm, to, actor):
    assert is_allowed(frm, to, actor) is True


@pytest.mark.parametrize(
    "frm,to,actor",
    [c for c in ALL_CASES if c not in EXPECTED_ALLOWED],
    ids=_id,
)
def test_transitions_not_in_table_are_rejected(frm, to, actor):
    assert is_allowed(frm, to, actor) is False


@pytest.mark.parametrize("frm", [None, NEW, DOING, CAND, UNDONE, CHECK], ids=_id)
def test_agent_can_never_set_done(frm):
    """US-04/05 실패 조건: Agent가 '완료'로 설정."""
    assert is_allowed(frm, DONE, AGENT) is False


@pytest.mark.parametrize("frm", [None, NEW, DOING, UNDONE, CHECK], ids=_id)
def test_done_only_reachable_from_done_candidate(frm):
    """'완료'는 '완료 후보'에서만 (사람이어도 다른 상태에서는 불가)."""
    assert is_allowed(frm, DONE, HUMAN) is False


def test_transition_error_is_an_exception_type():
    assert issubclass(TransitionError, Exception)


@pytest.mark.parametrize("actor", list(Actor), ids=_id)
@pytest.mark.parametrize("status", list(Status), ids=_id)
def test_self_transition_is_never_a_state_transition(status, actor):
    """SPEC 5.6: 자기 전이는 상태 전이가 아니다 (재판정 기록은 별도 경로)."""
    assert is_allowed(status, status, actor) is False


def test_agent_cannot_change_done_candidate_state():
    """SPEC 5.6: 완료 후보는 사람의 판단 대기 상태이며 Agent는 상태를 바꿀 수 없다."""
    for to in Status:
        assert is_allowed(CAND, to, AGENT) is False


def test_only_human_can_withdraw_done():
    """SPEC 5.6: 완료 철회(완료→진행중)는 사람만."""
    assert is_allowed(DONE, DOING, HUMAN) is True
    assert is_allowed(DONE, DOING, AGENT) is False


@pytest.mark.parametrize("to", [NEW, CHECK], ids=_id)
def test_human_cannot_create_action(to):
    """SPEC 3.2, 5.2: 최초 생성 주체는 Agent뿐 (MVP)."""
    assert is_allowed(None, to, HUMAN) is False
