"""T-10 Human Review: 사람의 최종 판단 (순수 deterministic, LLM 호출 없음).

- 상태 전이·완료 가드·history 기록은 모두 기존 ActionStore.change_status()(T-03-3)가 수행한다.
  이 모듈은 선택지 → 상태 매핑, 입력 검증, change_status 호출만 하는 얇은 orchestration이다.
- 주체는 항상 사람이다. Agent용 경로는 없다.
- 선택지는 건별 3개뿐이다 (일괄 없음).
"""
from datetime import datetime

from meeting_action_agent.state import Actor, Status, TransitionError
from meeting_action_agent.store import ActionStore

CHOICE_DONE = "완료"
CHOICE_CONTINUE = "계속 진행"
CHOICE_NEEDS_CHECK = "확인 필요"

# 선택지 → 상태 (✅ 완료 / 🟠 진행중 / 🟡 확인 필요). 순서가 곧 노출 순서.
CHOICES = {
    CHOICE_DONE: Status.DONE,
    CHOICE_CONTINUE: Status.IN_PROGRESS,
    CHOICE_NEEDS_CHECK: Status.NEEDS_CHECK,
}


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}은(는) 비어 있거나 공백일 수 없다")
    return value.strip()


def review_action(store: ActionStore, action_id, choice, *, actor_name,
                  meeting_id=None, timestamp=None, approved_at=None) -> None:
    """사람의 판단 1건을 적용한다.

    - choice: '완료' | '계속 진행' | '확인 필요' (그 외 ValueError)
    - actor_name: 필수 (None·빈 값·공백 ValueError)
    - '완료'는 meeting_id·timestamp(승인 근거)가 필수. approved_at 생략 시 현재 시각 (테스트는 주입)
    - '완료 후보'가 아닌 Action, 이미 '완료'인 Action은 change_status가 거부한다 (TransitionError)
    - 이전 승인과 같은 값의 재사용은 change_status가 거부한다 (CompletionGuardError)
    """
    if choice not in CHOICES:
        raise ValueError(f"허용되지 않는 선택지: {choice!r} (허용: {', '.join(CHOICES)})")
    name = _text(actor_name, "승인자(actor_name)")

    if choice == CHOICE_DONE:
        approval = {
            "approver": name,
            "approved_at": approved_at or datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": {
                "meeting_id": _text(meeting_id, "meeting_id"),
                "timestamp": _text(timestamp, "timestamp"),
            },
        }
        store.change_status(action_id, Status.DONE, Actor.HUMAN, approved_by_human=approval,
                            actor_name=name, note=f"사람 판단: {choice}")
        return

    store.change_status(action_id, CHOICES[choice], Actor.HUMAN,
                        actor_name=name, note=f"사람 판단: {choice}")


def withdraw_completion(store: ActionStore, action_id, *, actor_name) -> None:
    """완료 철회 (✅ → 🟠). 사람 전용. 철회되는 승인 스냅샷은 change_status가 history에 남기고
    approved_by_human은 보존한다. '완료'가 아닌 Action은 철회 대상이 아니다 (TransitionError)."""
    if store.get(action_id)["status"] != Status.DONE.value:
        raise TransitionError("완료 철회는 '완료' Action에만 가능하다")
    store.change_status(action_id, Status.IN_PROGRESS, Actor.HUMAN,
                        actor_name=_text(actor_name, "철회자(actor_name)"), note="사람 판단: 완료 철회")
