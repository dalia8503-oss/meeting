"""T-09 검증된 FollowupResult를 Action 상태에 반영한다 (domain/state slice).

순서(Action 1건): FollowupResult 검증 → Reviewer → (REJECT면 종료, 쓰기 없음) → Safety Guard
                  → 기존 change_status() 또는 record_reassessment() 한 번 → history.
- 별도 상태 머신을 만들지 않는다. 전이 허용 여부는 기존 전이표(is_allowed)와 ActionStore가 결정한다.
- Agent는 '완료'를 만들 수 없다. '완료 후보'까지만 가능하다.
- 이미 '완료 후보'인 Action은 상태를 바꾸지 않고 재판정만 기록한다. '완료' Action은 건드리지 않는다.
- 기한경과는 상태가 아니라 판정 보조 정보다. 기한 변경 제안은 기존 due_date를 바꾸지 않는다.
- 기한경과·기한변경제안은 Action의 flags/flag_notes(기존 contract)에 부착하며 history note와 별개로 남긴다(중복 flag 없음).
- 근거 없고 상태도 안 바뀌는 점검은 history 항목이 없으므로 flags도 쓰지 않는다.
- 미언급(근거 없음) 점검은 기한경과여도 어떤 write도 하지 않는다(상태·flags·history 불변). "기한경과+미언급" 알림은 향후 별도 기능.
- Reviewer REJECT 시 재분석은 1회만(apply_followup_with_retry): 최종 Reviewer PASS 이전에는 어떤 write도 없다.
- 쓰기는 Action 1건당 ActionStore 호출 1회(검증 후 파일 1회 기록)다. 전체 회의 단위 트랜잭션은 범위 밖.
"""
import copy
import json
from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.contracts import SchemaValidationError
from meeting_action_agent.manager.followup import FollowupValidationError, build_prompt, validate_followup
from meeting_action_agent.reviewer import ReviewOutputError, review_followup
from meeting_action_agent.state import Actor, CompletionGuardError, Status, is_allowed

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
    followup_calls: int = 1       # Follow-up 판정 수(첫 결과 포함). 재분석 시 2
    review_calls: int = 1         # Reviewer 호출 수


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


def _flag_updates(action: Mapping, result: Mapping, current_meeting_date: str) -> tuple:
    """(flags, flag_notes). 기한경과는 is_overdue(CHK024)일 때만, 기한변경제안은 새 기한 원문이 있을 때만.

    기한경과 note는 회의와 무관한 고정 문구라 반복 점검해도 중복되지 않는다(회의별 기록은 history).
    """
    flags, notes = [], {}
    if is_overdue(action, current_meeting_date):
        flags.append("기한경과")
        notes["기한경과"] = {"note": f"기한 {action['due_date_normalized']} 경과"}
    proposed = result.get("proposed_due_date")
    if proposed and "기한변경제안" in result.get("flags_to_add", ()):
        flags.append("기한변경제안")
        info = {"note": proposed}
        if result.get("evidence"):
            info["evidence"] = copy.deepcopy(result["evidence"])
        notes["기한변경제안"] = info
    return flags, notes


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


def _review(action: Mapping, result: Mapping, transcript, reviewer_runner, attempt: int) -> dict:
    """검증(실패 시 예외) → Reviewer. 쓰기 없음."""
    validate_followup(result, action, transcript)
    return review_followup(copy.deepcopy(action), transcript, copy.deepcopy(dict(result)),
                           reviewer_runner, attempt=attempt)


def _commit(store, action: Mapping, result: Mapping, review: dict, current_meeting_date: str) -> ApplyOutcome:
    """Reviewer PASS 후에만 호출한다. Safety Guard → 상태 변경 또는 재판정 1회."""
    current = action["status"]
    aid = action["action_id"]
    evidence = copy.deepcopy(result["evidence"])
    if not evidence:
        # 미언급: 기한경과여도 flags·flag_notes·history·상태 모두 불변 (기한경과+미언급 알림은 별도 기능)
        return ApplyOutcome(aid, "skipped", current, current, "미언급(근거 없음): Action 변경 없음", review)

    target, guard_note = _target_status(action, result)
    note = _note(action, result, current_meeting_date, guard_note)
    flags, flag_notes = _flag_updates(action, result, current_meeting_date)

    can_transition = (
        current != Status.DONE_CANDIDATE.value  # 완료 후보는 Agent 상태 변경 locked
        and target != current
        and is_allowed(current, target, Actor.AGENT)
    )
    if can_transition:
        store.change_status(aid, target, Actor.AGENT.value, evidence=evidence, note=note,
                            flags=flags, flag_notes=flag_notes)
        return ApplyOutcome(aid, "applied", current, target, note, review)
    store.record_reassessment(aid, evidence=evidence, note=note, flags=flags, flag_notes=flag_notes)
    return ApplyOutcome(aid, "reassessed", current, current, note, review)


def apply_followup(store, result: Mapping, transcript: Sequence[Mapping],
                   reviewer_runner: Callable[[str], Union[str, dict]],
                   current_meeting_date: str, *, attempt: int = 0) -> ApplyOutcome:
    """FollowupResult 1건을 검증·Reviewer 통과 후 반영한다. 검증 실패는 예외로 전파하며 쓰기 없음."""
    action = store.get(result["action_id"])  # 읽기만
    current = action["status"]
    if current == Status.DONE.value:
        return ApplyOutcome(action["action_id"], "skipped", current, current, "완료 Action은 이행점검 반영 대상이 아님")
    review = _review(action, result, transcript, reviewer_runner, attempt)
    if review["verdict"] != "PASS":
        return ApplyOutcome(action["action_id"], "rejected", current, current, "Reviewer REJECT", review)
    return _commit(store, action, result, review, current_meeting_date)


@dataclass(frozen=True)
class PreparedFollowup:
    """Reviewer(+재분석 1회)까지 끝난 Follow-up 1건. read-only 산출물이다 — store write는 commit_prepared가 한다.

    final이 있으면 더 쓸 것이 없는 종결 결과(skipped | rejected)이고, 없으면 PASS라 commit 대상이다.
    """
    action_id: str
    action: Optional[dict] = None       # 점검 시점의 Action 스냅샷 (commit이 그대로 사용)
    result: Optional[dict] = None       # 최종 PASS된 FollowupResult
    review: Optional[dict] = None
    followup_calls: int = 1
    review_calls: int = 1
    final: Optional[ApplyOutcome] = None


def prepare_followup_with_retry(store, first_result: Mapping, transcript: Sequence[Mapping],
                                followup_runner: Callable[[str], Union[str, dict]],
                                reviewer_runner: Callable[[str], Union[str, dict]]) -> PreparedFollowup:
    """T-09-7의 검증 구간. 첫 FollowupResult → Reviewer #1. REJECT면 Follow-up 재분석 딱 1회 → Reviewer #2.

    - store는 읽기만 한다(write 없음). 따라서 다른 Branch와 동시에 실행해도 파일 write 경합이 없다.
    - 호출 상한: Follow-up 판정 2회(첫 결과 + 재분석 1), Reviewer 2회. 그 이상은 없다.
    - 재분석 prompt에는 Previous Action·Current Transcript·1차 결과·Reviewer REJECT 사유가 들어간다.
      사유는 참고정보일 뿐이고, 2차 결과도 1차와 같은 validate_followup을 전부 다시 통과해야 한다.
    - 2차 결과가 invalid면 Reviewer #2·3차 분석 없이 실패 종료한다.
    - 첫 결과가 invalid면(Reviewer 판정 없음) 재분석 없이 예외를 전파한다(apply_followup과 동일).
    """
    action = store.get(first_result["action_id"])  # 읽기만
    current = action["status"]
    aid = action["action_id"]
    if current == Status.DONE.value:
        return PreparedFollowup(aid, final=ApplyOutcome(
            aid, "skipped", current, current, "완료 Action은 이행점검 반영 대상이 아님"))

    review = _review(action, first_result, transcript, reviewer_runner, 0)
    if review["verdict"] == "PASS":
        return PreparedFollowup(aid, action, dict(first_result), review)

    retry = {"previous_result": copy.deepcopy(dict(first_result)),
             "reviewer_reasons": copy.deepcopy(review["reasons"])}
    raw = followup_runner(build_prompt(copy.deepcopy(action), copy.deepcopy(list(transcript)), retry))
    try:
        second = json.loads(raw) if isinstance(raw, str) else raw
        validate_followup(second, action, transcript)  # 1차와 동일한 전체 검증
    except (json.JSONDecodeError, SchemaValidationError, FollowupValidationError, CompletionGuardError) as e:
        return PreparedFollowup(aid, final=ApplyOutcome(
            aid, "rejected", current, current, f"재분석 결과 invalid: {e}", review, followup_calls=2, review_calls=1))

    review2 = _review(action, second, transcript, reviewer_runner, 1)
    if review2["verdict"] != "PASS":
        return PreparedFollowup(aid, final=ApplyOutcome(
            aid, "rejected", current, current, "Reviewer REJECT (재분석 후)", review2,
            followup_calls=2, review_calls=2))
    return PreparedFollowup(aid, action, dict(second), review2, followup_calls=2, review_calls=2)


def commit_prepared(store, prepared: PreparedFollowup, current_meeting_date: str) -> ApplyOutcome:
    """PASS된 PreparedFollowup을 반영한다(Safety Guard → 상태 변경/재판정 1회). 종결 결과면 그대로 반환."""
    if prepared.final is not None:
        return prepared.final
    out = _commit(store, prepared.action, prepared.result, prepared.review, current_meeting_date)
    return ApplyOutcome(out.action_id, out.outcome, out.from_status, out.to_status, out.detail, out.review,
                        followup_calls=prepared.followup_calls, review_calls=prepared.review_calls)


def apply_followup_with_retry(store, first_result: Mapping, transcript: Sequence[Mapping],
                              followup_runner: Callable[[str], Union[str, dict]],
                              reviewer_runner: Callable[[str], Union[str, dict]],
                              current_meeting_date: str) -> ApplyOutcome:
    """T-09-7. 검증 구간(prepare_followup_with_retry) → 최종 PASS만 반영(commit_prepared)."""
    return commit_prepared(
        store, prepare_followup_with_retry(store, first_result, transcript, followup_runner, reviewer_runner),
        current_meeting_date)


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


def apply_followups_with_retry(store, results: Sequence[Mapping], transcript: Sequence[Mapping],
                               followup_runner, reviewer_runner, current_meeting_date: str) -> list:
    """Action별 독립 retry. 한 건의 실패·REJECT가 다른 Action 반영을 막지 않는다."""
    outcomes = []
    for r in results:
        try:
            outcomes.append(apply_followup_with_retry(store, r, transcript, followup_runner, reviewer_runner,
                                                      current_meeting_date))
        except (FollowupValidationError, SchemaValidationError, CompletionGuardError) as e:
            outcomes.append(ApplyOutcome(r.get("action_id", "?"), "rejected", detail=str(e)))
        except ReviewOutputError as e:
            # Reviewer 출력 자체가 invalid(정상 REJECT 아님): 해당 Action만 fail-closed. write·retry 없음.
            # 이 예외만 격리한다 — store write 실패·프로그램 오류 등은 삼키지 않는다.
            outcomes.append(ApplyOutcome(r.get("action_id", "?"), "rejected",
                                         detail=f"Reviewer 출력 invalid(fail-closed): {e}"))
    return outcomes


_ISOLATED = (FollowupValidationError, SchemaValidationError, CompletionGuardError)


def prepare_followups_with_retry(store, results: Sequence[Mapping], transcript: Sequence[Mapping],
                                 followup_runner, reviewer_runner) -> list:
    """Action별 독립 검증 구간(read-only). 실패·REJECT·Reviewer 출력 invalid는 해당 Action만 rejected로 격리한다.

    apply_followups_with_retry와 같은 격리 정책이며(그 외 예외는 삼키지 않는다) store write가 없다.
    """
    prepared = []
    for r in results:
        aid = r.get("action_id", "?")
        try:
            prepared.append(prepare_followup_with_retry(store, r, transcript, followup_runner, reviewer_runner))
        except _ISOLATED as e:
            prepared.append(PreparedFollowup(aid, final=ApplyOutcome(aid, "rejected", detail=str(e))))
        except ReviewOutputError as e:
            prepared.append(PreparedFollowup(aid, final=ApplyOutcome(
                aid, "rejected", detail=f"Reviewer 출력 invalid(fail-closed): {e}")))
    return prepared


def commit_prepared_followups(store, prepared: Sequence[PreparedFollowup], current_meeting_date: str) -> list:
    """검증이 끝난 Follow-up을 순서대로(단일 스레드) 반영한다. 반영 중 도메인 검증 실패는 해당 Action만 rejected."""
    outcomes = []
    for p in prepared:
        try:
            outcomes.append(commit_prepared(store, p, current_meeting_date))
        except _ISOLATED as e:
            outcomes.append(ApplyOutcome(p.action_id, "rejected", detail=str(e)))
    return outcomes
