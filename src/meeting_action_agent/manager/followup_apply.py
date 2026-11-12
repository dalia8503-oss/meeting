"""T-09 검증된 FollowupResult를 Action 상태에 반영한다 (domain/state slice).

순서(Action 1건): FollowupResult 검증 → Reviewer → (REJECT면 종료, 쓰기 없음) → Safety Guard
                  → 기존 change_status() 또는 record_reassessment() 한 번 → history.
- 별도 상태 머신을 만들지 않는다. 전이 허용 여부는 기존 전이표(is_allowed)와 ActionStore가 결정한다.
- Agent는 '완료'를 만들 수 없다. '완료 후보'까지만 가능하다.
- 이미 '완료 후보'인 Action은 상태를 바꾸지 않고 재판정만 기록한다. '완료' Action은 건드리지 않는다.
- 기한경과는 상태가 아니라 판정 보조 정보다. 기한 변경 제안은 기존 due_date를 바꾸지 않고 기록만 한다.
- 쓰기는 Action 1건당 ActionStore 호출 1회(검증 후 파일 1회 기록)다. 전체 회의 단위 트랜잭션은 범위 밖.
"""
import copy
from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.contracts import SchemaValidationError
from meeting_action_agent.manager.followup import FollowupValidationError, validate_followup
from meeting_action_agent.reviewer import review_followup
from meeting_action_agent.state import Actor, Status, is_allowed

_NEEDS_CHECK = "확인 필요"
_PENDING = "미정"


@dataclass(frozen=True)
class ApplyOutcome:
    action_id: str
    outcome: str                  # applied | reassessed | rejected | skipped
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    detail: str = ""
    review: Optional[dict] = None


def is_overdue(action: Mapping, current_meeting_date: str) -> bool:
    """기한경과 판정 보조 (CHK024). due_date_normalized가 있고 현재 회의일이 그보다 *이후*일 때만 True.

    같은 날은 False. 정규화 값이 없거나(raw만 있음·미정·확인 필요·해석 불가) 날짜 형식이 아니면 False(판단 불가).
    """
    due = action.get("due_date_normalized")
    if not due or action.get("due_date") in (_PENDING, _NEEDS_CHECK):
        return False
    try:
        return date.fromisoformat(current_meeting_date) > date.fromisoformat(due)
    except (TypeError, ValueError):
        return False


def _target_status(action: Mapping, result: Mapping) -> tuple:
    """Safety Guard. (목표 status, 사유 메모) — 담당자 확인 필요는 완료 후보 불가 (CHK020)."""
    proposed = result["proposed_status"]
    if proposed == Status.DONE_CANDIDATE.value and action.get("assignee") == _NEEDS_CHECK:
        return Status.NEEDS_CHECK.value, "담당자 확인 필요: 완료 근거가 있어도 완료 후보로 올리지 않음(CHK020)"
    return proposed, ""


def _note(action: Mapping, result: Mapping, current_meeting_date: str, guard_note: str) -> str:
    parts = [
        f"이행점검 {result['meeting_id']}: 제안={result['proposed_status']} 확신도={result['confidence']}",
        f"사유={result['reason']}",
    ]
    if guard_note:
        parts.append(guard_note)
    if is_overdue(action, current_meeting_date):
        parts.append(f"기한경과(기한 {action['due_date_normalized']} < 회의일 {current_meeting_date}) — 상태 근거 아님")
    if result.get("proposed_due_date"):
        parts.append(f"기한변경제안: {result['proposed_due_date']} (기존 기한 {action['due_date']} 유지, 사람 승인 필요)")
    return " | ".join(parts)


def apply_followup(store, result: Mapping, transcript: Sequence[Mapping],
                   reviewer_runner: Callable[[str], Union[str, dict]],
                   current_meeting_date: str, *, attempt: int = 0) -> ApplyOutcome:
    """FollowupResult 1건을 검증·Reviewer 통과 후 반영한다. 검증 실패는 예외로 전파하며 쓰기 없음."""
    action = store.get(result["action_id"])  # 읽기만
    current = action["status"]
    if current == Status.DONE.value:
        return ApplyOutcome(action["action_id"], "skipped", current, current, "완료 Action은 이행점검 반영 대상이 아님")

    validate_followup(result, action, transcript)  # 실패 시 예외, 쓰기 없음
    review = review_followup(copy.deepcopy(action), transcript, copy.deepcopy(dict(result)),
                             reviewer_runner, attempt=attempt)
    if review["verdict"] != "PASS":
        return ApplyOutcome(action["action_id"], "rejected", current, current, "Reviewer REJECT", review)

    target, guard_note = _target_status(action, result)
    note = _note(action, result, current_meeting_date, guard_note)
    evidence = copy.deepcopy(result["evidence"])
    aid = action["action_id"]

    can_transition = (
        current != Status.DONE_CANDIDATE.value  # 완료 후보는 Agent 상태 변경 locked
        and target != current
        and is_allowed(current, target, Actor.AGENT)
    )
    if can_transition:
        store.change_status(aid, target, Actor.AGENT.value, evidence=evidence or None, note=note)
        return ApplyOutcome(aid, "applied", current, target, note, review)
    if evidence:
        store.record_reassessment(aid, evidence=evidence, note=note)
        return ApplyOutcome(aid, "reassessed", current, current, note, review)
    # 근거 없는 재판정은 기록할 수 없고 상태도 그대로다 (언급 없음 + 이미 확인 필요 등)
    return ApplyOutcome(aid, "skipped", current, current, "근거 없음: 상태 변경·재판정 기록 없음", review)


def apply_followups(store, results: Sequence[Mapping], transcript: Sequence[Mapping],
                    reviewer_runner, current_meeting_date: str) -> list:
    """Action별로 apply_followup. 한 건의 검증 실패가 다른 Action 반영을 막지 않는다(실패는 rejected로 보고)."""
    outcomes = []
    for r in results:
        try:
            outcomes.append(apply_followup(store, r, transcript, reviewer_runner, current_meeting_date))
        except (FollowupValidationError, SchemaValidationError) as e:
            outcomes.append(ApplyOutcome(r.get("action_id", "?"), "rejected", detail=str(e)))
    return outcomes
