"""Reviewer 실행 계층 (T-06-1~4 최소 slice) — read-only 검증.

흐름: (ActionCandidate, 원본 Utterance) → 코드(결정적) 검증 → runner(LLM, 의미 검증) → review-result 계약 검증.
- Reviewer는 후보를 고치지 않는다. 결과는 PASS/REJECT + 사유뿐이다.
- 코드 검증이 실패하면 runner를 부르지 않고 REJECT한다. runner가 PASS를 내도 코드 REJECT를 뒤집지 못한다.
- runner 출력이 계약 위반(수정안 같은 추가 필드 포함)이면 ReviewOutputError — 아무것도 통과시키지 않는다(fail-closed).
- runner는 주입되는 callable(prompt:str) -> str(JSON) | dict.
"""
import json
import re
from pathlib import Path
from typing import Callable, Mapping, Optional, Union

from meeting_action_agent.contracts import CONTRACTS_DIR, SchemaValidationError, receive_agent_output
from meeting_action_agent.speakers import speaker_context_text

AGENT_DEFINITION = Path(__file__).resolve().parents[3] / ".claude" / "agents" / "reviewer.md"
_NO_EVIDENCE_NEEDED = {"확인 필요", "미정"}


class ReviewOutputError(ValueError):
    """Reviewer 출력이 JSON/계약/대상 일치 검증을 통과하지 못함. 판정으로 인정하지 않는다."""


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "")


def build_prompt(candidate: Mapping, utterance: Mapping, attempt: int = 0) -> str:
    agent = AGENT_DEFINITION.read_text(encoding="utf-8")
    schema = (CONTRACTS_DIR / "review-result.schema.json").read_text(encoding="utf-8")
    return (
        f"{agent}\n\n## 출력 스키마 (review-result.schema.json)\n{schema}\n\n"
        f"## ActionCandidate\n{json.dumps(candidate, ensure_ascii=False, indent=1)}\n\n"
        f"## 근거 원본 발언\n{json.dumps(dict(utterance), ensure_ascii=False, indent=1)}\n\n"
        f"attempt={attempt}. 수정하지 말고 PASS/REJECT 판정 JSON 하나만 출력하라."
    )


def code_check(candidate: Mapping, utterance: Mapping) -> list:
    """결정적 검증. 위반 사유 목록(빈 목록이면 통과)."""
    reasons = []
    text = utterance.get("text") or ""
    pool = _norm(text) + "".join(_norm(q.get("text", "")) for q in candidate.get("extra_evidence") or [])
    if candidate.get("timestamp") != utterance.get("timestamp"):
        reasons.append({"code": "타임스탬프불일치", "detail": "후보 timestamp가 근거 발언과 다르다"})
    if not _norm(candidate.get("evidence", "")) or _norm(candidate["evidence"]) not in _norm(text):
        reasons.append({"code": "인용불일치", "detail": "evidence가 근거 발언 text의 부분 문자열이 아니다"})
    assignee = candidate.get("assignee", "")
    if assignee not in _NO_EVIDENCE_NEEDED and _norm(assignee) not in pool:
        reasons.append({"code": "허위담당자", "detail": f"담당자 '{assignee}'가 근거 발언에 없다"})
    due = candidate.get("due_date", "")
    if due not in _NO_EVIDENCE_NEEDED and _norm(due) not in pool:
        reasons.append({"code": "허위기한", "detail": f"기한 '{due}'가 근거 발언에 없다"})
    if candidate.get("due_date_normalized") is not None:
        reasons.append({"code": "허위환산날짜", "detail": "Analyst는 환산 날짜를 채울 수 없다(코드 담당)"})
    return reasons


def review_candidate(
    candidate: Mapping,
    utterance: Mapping,
    runner: Callable[[str], Union[str, dict]],
    *,
    attempt: int = 0,
) -> dict:
    """후보 1건을 판정한다. 반환: review-result 계약을 만족하는 dict. 후보는 변경하지 않는다."""
    ref = candidate["timestamp"]
    failed = code_check(candidate, utterance)
    if failed:
        return {"target": {"type": "action", "ref": ref}, "verdict": "REJECT", "reasons": failed, "attempt": attempt}

    raw = runner(build_prompt(candidate, utterance, attempt))
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
        receive_agent_output("review-result", result)
    except (json.JSONDecodeError, SchemaValidationError) as e:
        raise ReviewOutputError(f"Reviewer 출력 거부: {e}") from e
    if result["target"] != {"type": "action", "ref": ref}:
        raise ReviewOutputError(f"Reviewer 대상 불일치: {result['target']} != action/{ref}")
    return result


# ----- T-09-7: FollowupResult 검증 (read-only) -----

def build_followup_prompt(action: Mapping, transcript, result: Mapping, attempt: int = 0,
                          speaker_map: Optional[Mapping] = None) -> str:
    schema = (CONTRACTS_DIR / "review-result.schema.json").read_text(encoding="utf-8")
    payload = {"previous_action": dict(action), "transcript": list(transcript), "followup_result": dict(result)}
    return (
        "# Reviewer - 이행점검 판정(FollowupResult) 검증\n"
        "PASS/REJECT와 사유만 낸다. 판정·근거·Action·상태·담당자·기한을 수정하거나 추측하지 않는다.\n"
        "- 판정이 근거와 일치하는가 / 근거가 transcript에 실재하는가\n"
        "- 완료 후보에 실제 완료형 표현이 있는가 (질문·제안·부정·미수행 표현을 완료로 오판하지 않았는가)\n"
        "- 담당자를 추측하지 않았는가 (화자 이름은 사용자 확인 speaker_map만 근거이며 직접 추론하지 않는다)\n\n"
        f"## 출력 스키마 (review-result.schema.json)\n{schema}\n\n"
        "## INPUT\n" + json.dumps(payload, ensure_ascii=False, indent=1)
        + speaker_context_text(speaker_map)
        + f"\n\nattempt={attempt}. target은 type=followup, ref=action_id. 판정 JSON 하나만 출력하라."
    )


def code_check_followup(action: Mapping, transcript, result: Mapping, speaker_map: Optional[Mapping] = None) -> list:
    """결정적 검증. 위반 사유 목록(빈 목록이면 통과). 입력은 변경하지 않는다."""
    from meeting_action_agent.manager.followup import FollowupValidationError, validate_followup
    from meeting_action_agent.state import CompletionGuardError

    try:
        validate_followup(result, action, transcript, speaker_map)
    except CompletionGuardError as e:
        return [{"code": "완료설정", "detail": str(e)}]
    except SchemaValidationError as e:
        return [{"code": "필수필드누락", "detail": str(e)}]
    except FollowupValidationError as e:
        code = "인용불일치" if "원문과 다름" in str(e) else "판정비도출"
        return [{"code": code, "detail": str(e)}]
    return []


def review_followup(action: Mapping, transcript, result: Mapping,
                    runner: Callable[[str], Union[str, dict]], *, attempt: int = 0,
                    speaker_map: Optional[Mapping] = None) -> dict:
    """FollowupResult 1건 판정. 입력은 변경하지 않는다 (복사본만 runner에 전달)."""
    import copy

    ref = result["action_id"]
    failed = code_check_followup(action, transcript, result, speaker_map)
    if failed:
        return {"target": {"type": "followup", "ref": ref}, "verdict": "REJECT", "reasons": failed, "attempt": attempt}
    raw = runner(build_followup_prompt(copy.deepcopy(dict(action)), copy.deepcopy(list(transcript)),
                                       copy.deepcopy(dict(result)), attempt, speaker_map))
    try:
        verdict = json.loads(raw) if isinstance(raw, str) else raw
        receive_agent_output("review-result", verdict)
    except (json.JSONDecodeError, SchemaValidationError) as e:
        raise ReviewOutputError(f"Reviewer 출력 거부: {e}") from e
    if verdict["target"] != {"type": "followup", "ref": ref}:
        raise ReviewOutputError(f"Reviewer 대상 불일치: {verdict['target']} != followup/{ref}")
    return verdict
