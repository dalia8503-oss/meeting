"""저장 계층 — T-03-3 범위: 상태 변경 단일 진입점과 완료 가드 (SPEC 5.6, 9장, US-05).

- 상태 변경은 change_status()만 통과한다 (전이표·완료 가드·history 기록).
- 파일을 읽을 때(load_all/get)도 같은 가드를 재검증한다 (직접 편집 우회 차단).
- 저장 형태: 단일 JSON 배열 파일 (파일 단위 결정은 T-00-1 미결이므로 경로만 받는다).
- 재판정 기록(record_reassessment, T-03-13)은 상태를 바꾸지 않고 history에만 남긴다.
- 스키마 검증(T-03-1)은 이 범위가 아니다.
- T-03-5: action_id 발급(next_action_id), 중복·재언급 판정(resolve_candidate), 추가 근거 보존(add_extra_evidence).
"""
import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meeting_action_agent.contracts import validate
from meeting_action_agent.state import (
    Actor,
    CompletionGuardError,
    Status,
    TransitionError,
    is_allowed,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_valid_approval(value) -> bool:
    """approved_by_human 구조 (contracts Approval): approver·approved_at·evidence(meeting_id, timestamp)."""
    if not isinstance(value, dict):
        return False
    approver, approved_at, evidence = (value.get(k) for k in ("approver", "approved_at", "evidence"))
    if not (isinstance(approver, str) and approver and isinstance(approved_at, str) and approved_at):
        return False
    return (
        isinstance(evidence, dict)
        and bool(evidence.get("meeting_id"))
        and bool(evidence.get("timestamp"))
    )


def _validate_history(action: dict) -> None:
    """history의 모든 상태 변경이 최초 생성 규칙·전이표를 따르고 이어지는지 검증한다 (SPEC 9장, T-03-14).

    재판정 항목(kind='재판정')은 상태 전이가 아니므로 전이표 검증과 구분한다:
    이전·이후 상태가 같고 그 시점의 현재 상태와 일치해야 한다.
    """
    action_id = action.get("action_id")
    current = None  # 직전까지의 상태 (최초 생성 전에는 None)
    history = action.get("history") or []
    if not history:
        raise TransitionError(f"{action_id}: 최초 생성 이력이 없음")
    for index, entry in enumerate(history):
        from_status, to_status = entry.get("from_status"), entry.get("to_status")
        if entry.get("kind") == "재판정":
            if index == 0 or from_status != to_status or from_status != current:
                raise TransitionError(f"{action_id}: 재판정 항목이 현재 상태와 맞지 않음 (#{index})")
            continue
        if from_status != current:
            raise TransitionError(f"{action_id}: history가 이어지지 않음 (#{index})")
        if not is_allowed(from_status, to_status, entry.get("actor")):
            raise TransitionError(f"{action_id}: 허용되지 않는 전이 이력 {from_status}→{to_status} (#{index})")
        current = to_status
    if current != action.get("status"):
        raise TransitionError(f"{action_id}: 마지막 이력 상태({current})와 status({action.get('status')})가 다름")


def _validate_stored_action(action: dict) -> None:
    """저장·로드 시 완료 가드와 history 검증."""
    history = action.get("history") or []
    if action.get("status") == Status.DONE.value:
        approval = action.get("approved_by_human")
        if not _is_valid_approval(approval):
            raise CompletionGuardError(f"{action.get('action_id')}: approved_by_human 없이 '완료'")
        done_entries = [e for e in history if e.get("to_status") == Status.DONE.value]
        last = done_entries[-1] if done_entries else None
        if (
            last is None
            or last.get("actor") != Actor.HUMAN.value
            or last.get("from_status") != Status.DONE_CANDIDATE.value
            or last.get("approval") != approval
        ):
            raise CompletionGuardError(
                f"{action.get('action_id')}: 사람의 '완료 후보'→'완료' 이력(승인 스냅샷)과 승인값이 일치하지 않음"
            )
    _validate_history(action)
    validate("action", action)  # 도메인/안전 가드를 모두 통과한 뒤 계약(JSON Schema) 검증


def _merge_flags(action: dict, flags, flag_notes) -> None:
    """플래그·부가 정보를 병합한다 (중복 방지). 호출자가 이후 검증·1회 저장을 한다.

    - flags: 이미 있는 값은 다시 추가하지 않는다(순서 유지).
    - flag_notes: 같은 플래그의 note가 같으면 유지, 다르면 ' / '로 이어 붙인다. evidence는 같은 인용을
      중복 없이 합친다(다른 회의 근거는 보존).
    """
    for flag in flags or ():
        if flag not in action["flags"]:
            action["flags"].append(flag)
    for flag, info in (flag_notes or {}).items():
        notes = action.setdefault("flag_notes", {})
        cur = notes.get(flag)
        if cur is None:
            notes[flag] = copy.deepcopy(info)
            continue
        if info["note"] not in cur["note"].split(" / "):
            cur["note"] = f"{cur['note']} / {info['note']}"
        for quote in info.get("evidence") or []:
            if quote not in cur.setdefault("evidence", []):
                cur["evidence"].append(copy.deepcopy(quote))
        if not cur.get("evidence"):
            cur.pop("evidence", None)


@dataclass(frozen=True)
class Resolution:
    """후보 판정 결과. kind: 'new' | 'duplicate'(같은 회의) | 'remention'(이전 회의 Action 재언급)."""
    kind: str
    action_id: str | None = None


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


class ActionStore:
    def __init__(self, path):
        self.path = Path(path)

    # ----- 파일 입출력 -----

    def _read(self) -> list:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, actions: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----- 공개 API -----

    def create(self, action: dict) -> None:
        """최초 생성. 주체는 Agent뿐이며 status는 전이표의 (없음)→신규/확인 필요만 허용한다."""
        action = copy.deepcopy(action)
        history = action.get("history") or []
        first = history[0] if history else {}
        actor = first.get("actor")
        if first.get("from_status") is not None or actor not in {a.value for a in Actor}:
            raise TransitionError("최초 history 항목이 없거나 형식이 올바르지 않음")
        if not is_allowed(None, action.get("status"), actor):
            raise TransitionError(f"최초 생성 불가: status={action.get('status')}, actor={actor}")
        _validate_stored_action(action)
        actions = self.load_all()
        actions.append(action)
        self._write(actions)

    def change_status(self, action_id, to_status, actor, *, approved_by_human=None,
                      evidence=None, note=None, actor_name=None,
                      flags=None, flag_notes=None) -> None:
        actions = self.load_all()  # 저장된 파일도 같은 가드로 재검증한 뒤 변경한다
        action = self._find(actions, action_id)
        from_status, to_status, actor = Status(action["status"]), Status(to_status), Actor(actor)

        if from_status == to_status:
            raise TransitionError("자기 전이는 상태 전이가 아니다 (재판정은 별도 경로)")
        if not is_allowed(from_status, to_status, actor):
            raise TransitionError(f"허용되지 않는 전이: {from_status.value}→{to_status.value} ({actor.value})")

        entry = {
            "at": _now(),
            "actor": actor.value,
            "from_status": from_status.value,
            "to_status": to_status.value,
        }
        if to_status is Status.DONE:
            # 그 요청에 새로 제출된 승인만 인정. 철회 후 남은 이전 승인값은 승인이 아니다.
            if not _is_valid_approval(approved_by_human):
                raise CompletionGuardError("유효한 approved_by_human 없이 '완료'로 변경할 수 없다")
            earlier = [action.get("approved_by_human")] + [
                e["approval"] for e in action["history"] if "approval" in e
            ]
            if approved_by_human in earlier:
                raise CompletionGuardError("이전 승인과 같은 값은 새 승인으로 인정하지 않는다")
            entry["approval"] = copy.deepcopy(approved_by_human)
        if from_status is Status.DONE:  # 완료 철회 (사람만, is_allowed가 보장)
            if not (isinstance(actor_name, str) and actor_name.strip()):
                raise ValueError("완료 철회에는 철회자 이름(actor_name)이 필요하다")
            entry["approval"] = copy.deepcopy(action["approved_by_human"])  # 철회되는 승인 스냅샷
        if actor_name:
            entry["actor_name"] = actor_name
        if evidence:
            entry["evidence"] = copy.deepcopy(evidence)
        if note:
            entry["note"] = note

        action["status"] = to_status.value
        if to_status is Status.DONE:
            action["approved_by_human"] = copy.deepcopy(approved_by_human)
        action["history"].append(entry)
        _merge_flags(action, flags, flag_notes)  # 상태·flags·history를 한 번에 저장
        validate("action", action)  # 변경 결과(evidence 등)가 계약을 만족해야 저장한다
        self._write(actions)  # 철회 시 approved_by_human은 지우지 않는다

    def record_reassessment(self, action_id, *, evidence, note, flags=None, flag_notes=None) -> None:
        """상태를 바꾸지 않는 재판정 기록 (SPEC 5.6, T-03-13). 주체는 Agent.

        완료 Action은 이행점검·재판정 대상이 아니므로 거부한다 (TransitionError).
        근거(evidence)와 사유(note)는 필수다 (ValueError).
        """
        actions = self.load_all()
        action = self._find(actions, action_id)
        status = Status(action["status"])
        if status is Status.DONE:
            raise TransitionError("완료 Action은 재판정 대상이 아니다")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("재판정에는 근거(evidence) 1개 이상이 필요하다")
        if not isinstance(note, str) or not note:
            raise ValueError("재판정에는 사유(note)가 필요하다")
        action["history"].append({
            "at": _now(),
            "actor": Actor.AGENT.value,
            "kind": "재판정",
            "from_status": status.value,
            "to_status": status.value,
            "evidence": copy.deepcopy(evidence),
            "note": note,
        })
        _merge_flags(action, flags, flag_notes)  # 재판정 기록과 flags를 한 번에 저장
        validate("action", action)
        self._write(actions)  # 상태·승인값은 바꾸지 않는다

    # ----- T-03-5: action_id 발급, 중복·재언급 -----

    def next_action_id(self) -> str:
        """저장된 최대 번호 다음 `A-NNN`. 저장 데이터만 기준이라 재로드해도 이어진다."""
        numbers = [
            int(m.group(1)) for a in self._read()
            if (m := re.fullmatch(r"A-(\d+)", str(a.get("action_id", ""))))
        ]
        return f"A-{max(numbers, default=0) + 1:03d}"

    def resolve_candidate(self, candidate: dict) -> Resolution:
        """'동일 담당자·업무'(assignee+task 일치, 공백 정규화만) 기준 (data-model 2.3).

        같은 meeting_id면 duplicate, 다른 회의의 기존 Action이면 remention(이행점검 대상), 없으면 new.
        저장·상태 변경은 하지 않는다.
        """
        key = (_norm(candidate["assignee"]), _norm(candidate["task"]))
        match = None
        for action in self.load_all():
            if (_norm(action["assignee"]), _norm(action["task"])) != key:
                continue
            if action["meeting_id"] == candidate["meeting_id"]:
                return Resolution("duplicate", action["action_id"])
            match = match or Resolution("remention", action["action_id"])
        return match or Resolution("new")

    def add_extra_evidence(self, action_id, quotes: list) -> None:
        """추가 근거를 extra_evidence 뒤에 덧붙인다. 기존 evidence·extra_evidence는 덮어쓰지 않는다."""
        actions = self.load_all()
        action = self._find(actions, action_id)
        action["extra_evidence"] = list(action.get("extra_evidence") or []) + copy.deepcopy(quotes)
        validate("action", action)
        self._write(actions)

    def get(self, action_id) -> dict:
        return self._find(self.load_all(), action_id)

    def load_all(self) -> list:
        actions = self._read()
        for action in actions:
            _validate_stored_action(action)
        return actions

    def remove_by_meeting(self, meeting_id: str) -> int:
        """meeting_id가 원천 회의인 Action만 제거한다 (사용자가 명시한 회의 삭제 전용). 제거 개수를 반환한다."""
        actions = self._read()
        kept = [a for a in actions if a.get("meeting_id") != meeting_id]
        if len(kept) != len(actions):
            self._write(kept)
        return len(actions) - len(kept)

    @staticmethod
    def _find(actions: list, action_id) -> dict:
        for action in actions:
            if action.get("action_id") == action_id:
                return action
        raise KeyError(action_id)
