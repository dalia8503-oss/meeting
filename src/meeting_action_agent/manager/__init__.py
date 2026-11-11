"""후보 → Action 변환 (T-03-12, Action Manager) — 기존 모듈을 연결하는 통합 계층.

새 규칙을 만들지 않는다. 재사용: contracts(검증), T-03-4/11 담당자·플래그, T-03-9 기한,
T-03-10 초기 status, T-03-5 중복/remention(ActionStore), ActionStore.create.

speaker·mentions는 ActionCandidate 필드가 아니라 변환 context(utterance, mentions)다.
화자를 추측·생성하지 않으며, candidate.assigner 를 speaker 대신 쓰지 않는다.
"""
import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from meeting_action_agent.contracts import receive_agent_output
from meeting_action_agent.rules.assignee import decide_assignee_flags
from meeting_action_agent.rules.due_date import decide_due_date
from meeting_action_agent.rules.status import decide_initial_status


class ConversionContextError(ValueError):
    """변환 context(utterance·metadata) 누락 또는 후보와의 불일치. 아무것도 저장하지 않는다."""


@dataclass(frozen=True)
class ActionOutcome:
    kind: str  # 'created' | 'duplicate'(같은 회의, extra_evidence 추가) | 'remention'(연결 결과만)
    action_id: str
    assignee: str


@dataclass(frozen=True)
class ConversionResult:
    outcomes: tuple  # tuple[ActionOutcome, ...] — 담당자(mentions) 순서


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _check_context(candidate: Mapping, utterance: Mapping, metadata: Mapping) -> None:
    for key in ("speaker", "timestamp", "text"):
        value = utterance.get(key) if isinstance(utterance, Mapping) else None
        if not (isinstance(value, str) and value.strip()):
            raise ConversionContextError(f"utterance.{key} 누락: 근거(EvidenceQuote)를 만들 수 없다")
    if utterance["timestamp"] != candidate["timestamp"]:
        raise ConversionContextError(
            f"candidate.timestamp({candidate['timestamp']})와 utterance.timestamp({utterance['timestamp']}) 충돌"
        )
    if utterance.get("meeting_id") not in (None, candidate["meeting_id"]):
        raise ConversionContextError("utterance.meeting_id 와 candidate.meeting_id 불일치")
    if metadata.get("meeting_id") not in (None, candidate["meeting_id"]):
        raise ConversionContextError("metadata.meeting_id 와 candidate.meeting_id 불일치")
    if not metadata.get("meeting_date"):
        raise ConversionContextError("metadata.meeting_date 누락: 기한 환산 기준일이 없다")


def convert_candidate(
    candidate: Mapping,
    *,
    utterance: Mapping,
    mentions: Optional[list] = None,
    metadata: Optional[Mapping] = None,
    reviewer_passed: bool = True,
    store,
) -> ConversionResult:
    """ActionCandidate 한 건을 담당자별 Action 생성 / 중복 근거 추가 / remention 연결로 변환한다."""
    metadata = metadata or {}
    receive_agent_output("action-candidate", candidate)  # 계약 + '완료' 제안 거부
    _check_context(candidate, utterance, metadata)

    names = list(mentions) if mentions else [candidate["assignee"]]
    flag_result = decide_assignee_flags(
        utterance_text=utterance["text"], speaker=utterance["speaker"], mentions=names, metadata=metadata
    )
    due = decide_due_date(raw=candidate["due_date"], meeting_date=metadata["meeting_date"])
    meeting_id = candidate["meeting_id"]
    quote = {
        "meeting_id": meeting_id, "timestamp": utterance["timestamp"],
        "speaker": utterance["speaker"], "text": utterance["text"],
    }

    outcomes = []
    for item in flag_result.per_assignee:
        resolution = store.resolve_candidate(
            {"meeting_id": meeting_id, "assignee": item.assignee, "task": candidate["task"]}
        )
        if resolution.kind == "duplicate":
            store.add_extra_evidence(resolution.action_id, [quote])
            outcomes.append(ActionOutcome("duplicate", resolution.action_id, item.assignee))
            continue
        if resolution.kind == "remention":  # 신규 생성·상태 변경·근거 추가 없음
            outcomes.append(ActionOutcome("remention", resolution.action_id, item.assignee))
            continue

        flags = list(dict.fromkeys([*candidate["flags"], *item.flags]))
        if not reviewer_passed and "Reviewer미통과" not in flags:
            flags.append("Reviewer미통과")
        status = decide_initial_status(
            assignee=item.assignee, due_date=due.due_date, flags=flags, reviewer_passed=reviewer_passed
        )
        # 안전 전파: 상위 단계에서 이미 나온 불확실성("확인 필요")을 변환 단계가 낮추지 않는다.
        if candidate.get("status") == "확인 필요":
            status = "확인 필요"
        action = {
            "action_id": store.next_action_id(),
            "meeting_id": meeting_id,
            "assigner": candidate["assigner"],
            "assignee": item.assignee,
            "task": candidate["task"],
            "due_date": due.due_date,
            "due_date_normalized": due.due_date_normalized,
            "evidence": utterance["text"],
            "extra_evidence": copy.deepcopy(candidate.get("extra_evidence") or []),
            "timestamp": utterance["timestamp"],
            "status": status,
            "flags": flags,
            "field_source": {},
            "approved_by_human": None,
            "history": [{"at": _now(), "actor": "Agent", "from_status": None, "to_status": status}],
        }
        notes = {**(candidate.get("flag_notes") or {}), **flag_result.flag_notes}
        notes = {k: v for k, v in notes.items() if k in flags}
        if notes:
            action["flag_notes"] = notes
        store.create(action)
        outcomes.append(ActionOutcome("created", action["action_id"], item.assignee))
    return ConversionResult(tuple(outcomes))
