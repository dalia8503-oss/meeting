"""T-BRIEF-INPUT: 직전 회의 브리핑용 입력(BriefingInput)을 결정적으로 구축한다.

- 100% 결정적 코드. LLM·TTS·API 없음. 저장된 사실을 선택·정렬·구조화만 하고 새 사실을 만들지 않는다.
- 직전 회의는 호출자가 명시한다(최신 회의를 추측하지 않는다). 기준 날짜(current_date)도 명시한다.
- Action은 분석 당시 snapshot이 아니라 ActionStore의 현재 상태를 사용한다.
- decisions는 오분류 가능성(T-SUMMARY-QA) 때문에 구조에만 남기고 include_in_briefing=False로 둔다.
- 완료(✅)는 approved_by_human이 있는 것만 completed_actions로 분리한다. 완료 후보는 완료가 아니다.
- 기한경과는 기존 규칙(CHK024, manager.followup_apply.is_overdue)을 재사용한다.
- transcript·analysis·actions·meta는 read-only다.
"""
import json
from datetime import date
from pathlib import Path

from meeting_action_agent.ingest import safe_meeting_id
from meeting_action_agent.manager.followup_apply import is_overdue
from meeting_action_agent.state import Status
from meeting_action_agent.store import ActionStore

_NEEDS_CHECK = Status.NEEDS_CHECK.value

# 표시 순서(결정적): 확인 필요 → 미완료 → 기한 초과 → 진행중 → 신규 → 완료 후보
_STATUS_RANK = {
    Status.NEEDS_CHECK.value: 0,
    Status.NOT_DONE.value: 1,
    Status.IN_PROGRESS.value: 3,
    Status.NEW.value: 4,
    Status.DONE_CANDIDATE.value: 5,
}
_OVERDUE_RANK = 2


class BriefingInputError(Exception):
    """직전 회의/기준 날짜/저장 데이터가 브리핑 입력으로 쓸 수 없는 경우."""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BriefingInputError(f"읽을 수 없는 파일: {path.name}") from exc


def _norm(text) -> str:
    return " ".join(str(text).split())


def _summary_items(items) -> list:
    return [{"text": i["text"], "evidence_timestamps": list(i["evidence_timestamps"])} for i in items or []]


def _action_item(a: dict, current_date: str) -> dict:
    status = a["status"]
    return {
        "action_id": a["action_id"],
        "assignee": a["assignee"],
        "task": a["task"],
        "due_date_raw": a["due_date"],
        "due_date_normalized": a["due_date_normalized"],
        "status": status,
        "overdue": is_overdue(a, current_date),
        "awaiting_human_confirmation": status == Status.DONE_CANDIDATE.value,  # 완료 후보: '완료'라 말하면 안 됨
        "flags": list(a.get("flags") or []),
        "evidence": {"meeting_id": a["meeting_id"], "timestamp": a["timestamp"], "text": a["evidence"]},
    }


def _sort_key(item: dict):
    rank = _STATUS_RANK[item["status"]]
    if item["overdue"] and rank > _OVERDUE_RANK:
        rank = _OVERDUE_RANK
    due = item["due_date_normalized"]
    return rank, due is None, due or "", item["action_id"]


def build_briefing_input(previous_meeting_id: str, *, current_date: str, data_root) -> dict:
    """직전 회의 1개에 대한 BriefingInput(dict, JSON 직렬화 가능)을 만든다.

    data_root: meetings/ 와 actions/actions.json 을 가진 저장 루트 (api.mode.get_data_root()와 같은 구조).
    """
    try:
        mid = safe_meeting_id(previous_meeting_id)
    except ValueError as exc:
        raise BriefingInputError("previous_meeting_id가 올바르지 않음") from exc
    try:
        date.fromisoformat(current_date)
    except (TypeError, ValueError) as exc:
        raise BriefingInputError("current_date는 YYYY-MM-DD여야 한다") from exc

    root = Path(data_root)
    meta_path = root / "meetings" / f"{mid}.meta.json"
    if not meta_path.is_file():
        raise BriefingInputError(f"직전 회의를 찾을 수 없음: {mid}")
    meta = _read_json(meta_path)
    analysis_path = root / "meetings" / f"{mid}.analysis.json"
    if not analysis_path.is_file():
        raise BriefingInputError(f"직전 회의가 아직 분석되지 않음: {mid}")
    analysis = _read_json(analysis_path)

    # Action은 현재 ActionStore 상태(load_all은 완료 가드·history를 재검증한다). 직전 회의에서 나온 Action만.
    actions = [a for a in ActionStore(root / "actions" / "actions.json").load_all() if a["meeting_id"] == mid]
    active, completed = [], []
    for a in actions:
        if a["status"] == Status.DONE.value:
            completed.append({**_action_item(a, current_date), "approved_by_human": a["approved_by_human"]})
        else:
            active.append(_action_item(a, current_date))
    active.sort(key=_sort_key)
    completed.sort(key=lambda c: c["action_id"])

    # 확인 필요: 분석의 needs_confirmation + '확인 필요' Action. 같은 항목은 한 번만.
    confirmations, seen_text = [], set()
    for item in _summary_items(analysis.get("needs_confirmation")):
        key = _norm(item["text"])
        if key in seen_text:
            continue
        seen_text.add(key)
        confirmations.append({"source": "analysis", **item})
    for item in active:
        if item["status"] == _NEEDS_CHECK:
            confirmations.append({"source": "action", "action_id": item["action_id"],
                                  "assignee": item["assignee"], "task": item["task"],
                                  "evidence": item["evidence"]})

    return {
        "previous_meeting": {"meeting_id": mid, "title": meta["title"], "meeting_date": meta["meeting_date"]},
        "current_date": current_date,
        "key_discussions": _summary_items(analysis.get("key_discussions")),
        "decisions": {"include_in_briefing": False, "items": _summary_items(analysis.get("decisions"))},
        "open_issues": _summary_items(analysis.get("open_issues")),
        "active_actions": active,
        "completed_actions": completed,
        "needs_confirmation": confirmations,
    }
