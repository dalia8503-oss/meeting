"""T-BRIEF-AGENT: BriefingInput → Meeting Briefing Agent → Briefing Reviewer → 검증된 briefing text.

- 입력은 build_briefing_input() 결과(dict)뿐이다. transcript·ActionStore·analysis를 직접 읽지 않는다.
- runner는 주입되는 callable(prompt:str) -> str(JSON) | dict (llm.py 계열, provider-neutral). TTS·API·저장 없음.
- 검증 순서: 코드(결정적) → LLM Reviewer(의미). 코드 REJECT는 LLM을 부르지 않고, LLM PASS가 뒤집지 못한다.
- Reviewer는 PASS/REJECT와 사유만 낸다(수정 불가). REJECT면 Reviewer 사유를 참고정보로 1회만 재생성한다.
- fail-closed: 승인(approved)되지 않은 briefing은 `briefing`으로 내보내지 않는다(rejected_draft에만 남는다).
- 입력 BriefingInput은 변경하지 않는다.
"""
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Union

_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"
BRIEFING_AGENT_DEFINITION = _AGENTS_DIR / "meeting-briefing.md"
BRIEFING_REVIEWER_DEFINITION = _AGENTS_DIR / "briefing-reviewer.md"

SECTION_KEYS = ("previous_summary", "active_actions", "confirmation_items")
REASON_CODES = frozenset({
    "구조오류", "사실생성", "완료후보오표현", "미승인완료", "decisions사용", "미결을결정으로",
    "기한초과오표현", "담당자오류", "기한오류", "상태왜곡", "없는Action참조",
})
MAX_BRIEFING_CHARS = 600  # 30~60초 TTS 상한 가드(약 300자가 권장). 의미 규칙이 아닌 길이 안전장치.
MAX_RETRIES = 1


class BriefingReviewerOutputError(ValueError):
    """Reviewer 출력이 계약(PASS/REJECT + 사유)을 만족하지 못함. 판정으로 인정하지 않는다."""


# ---------------------------------------------------------------- 생성

def build_briefing_prompt(briefing_input: Mapping, retry: Optional[Mapping] = None) -> str:
    agent = BRIEFING_AGENT_DEFINITION.read_text(encoding="utf-8")
    retry_text = ""
    if retry is not None:
        retry_text = (
            "\n## 재생성 참고정보 (Reviewer REJECT)\n"
            "아래는 1차 브리핑과 REJECT 사유다. 참고정보일 뿐 사실의 근거가 아니다. "
            "사실은 반드시 BriefingInput에서만 가져온다.\n"
            + json.dumps({"previous_briefing": retry["previous_briefing"],
                          "reviewer_reasons": retry["reviewer_reasons"]}, ensure_ascii=False, indent=1) + "\n"
        )
    return (
        f"{agent}\n\n## INPUT (BriefingInput)\n"
        + json.dumps(briefing_input, ensure_ascii=False, indent=1)
        + retry_text + "\n\n브리핑 JSON 하나만 출력하라."
    )


def _parse(raw) -> dict:
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("JSON object가 아님")
    return data


# ---------------------------------------------------------------- 코드 검증

def _reason(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def _norm(text) -> str:
    return re.sub(r"\s+", "", str(text))


_COMPLETION_OK = re.compile(
    r"완료\s*후보|완료\s*여부|완료\s*보고|완료\s*확인|최종\s*완료|완료\s*전|완료되지|완료하지|완료가\s*아|완료\s*승인")
_COMPLETION_CLAIM = re.compile(
    r"완료\s*(?:되었|됐|했|하였|되어|됨|처리|된|한|입니다|이?다\b)|끝났|끝냈|마쳤|마무리\s*(?:되었|됐|했)|✅")
_DECISION_CLAIM = re.compile(r"결정\s*(?:했|하였|되었|됐|됨|사항|이\s*났)|확정\s*(?:했|되었|됐|됨)|합의\s*(?:했|되었|됐)")
_OVERDUE_CLAIM = re.compile(r"기한\s*(?:이\s*)?(?:지났|초과|경과)|지연\s*(?:되었|됐|중)|늦어")
_IN_PROGRESS_CLAIM = re.compile(r"진행\s*중")
# 성+직함("김대리") 또는 이름+호칭("민수 씨") 전체 토큰. 앞이 한글이면 단어 일부로 보고 제외한다.
_PERSON = re.compile(r"(?<![가-힣])([가-힣]{1,3}(?:대리|과장|팀장|부장|차장|사원|매니저)|[가-힣]{2,3}\s?(?:씨|님))")
_MD = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _all_text(briefing_input: Mapping) -> str:
    return json.dumps(briefing_input, ensure_ascii=False)


def _allowed_month_days(briefing_input: Mapping) -> set:
    allowed = set()
    blob = _all_text(briefing_input)
    for iso in _ISO.findall(blob):
        allowed.add((int(iso[5:7]), int(iso[8:10])))
    for m, d in _MD.findall(blob):
        allowed.add((int(m), int(d)))
    return allowed


def validate_briefing_structure(briefing) -> list:
    """계약(briefing_text, sections, referenced_action_ids, 추가 필드 금지) 위반 사유 목록."""
    if not isinstance(briefing, dict):
        return [_reason("구조오류", "briefing이 object가 아님")]
    reasons = []
    extra = set(briefing) - {"briefing_text", "sections", "referenced_action_ids"}
    if extra:
        reasons.append(_reason("구조오류", f"허용되지 않은 필드: {sorted(extra)}"))
    text = briefing.get("briefing_text")
    if not isinstance(text, str) or not text.strip():
        reasons.append(_reason("구조오류", "briefing_text가 비어 있음"))
    elif len(text) > MAX_BRIEFING_CHARS:
        reasons.append(_reason("구조오류", f"briefing_text가 너무 김({len(text)}자 > {MAX_BRIEFING_CHARS}자, 30~60초 초과)"))
    sections = briefing.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTION_KEYS) \
            or not all(isinstance(v, str) for v in sections.values()):
        reasons.append(_reason("구조오류", f"sections는 문자열 값의 {list(SECTION_KEYS)}여야 함"))
    ids = briefing.get("referenced_action_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        reasons.append(_reason("구조오류", "referenced_action_ids는 중복 없는 문자열 배열이어야 함"))
    return reasons


def code_check_briefing(briefing_input: Mapping, briefing) -> list:
    """결정적 검증. 위반 사유 목록(빈 목록이면 통과). 입력은 변경하지 않는다."""
    reasons = validate_briefing_structure(briefing)
    if reasons:
        return reasons  # 구조가 깨졌으면 이후 검사를 신뢰할 수 없다

    text = briefing["briefing_text"]
    spoken = " ".join([text, *briefing["sections"].values()])
    active = briefing_input["active_actions"]
    completed = briefing_input["completed_actions"]

    # J. 존재하지 않는 Action ID
    known = {a["action_id"] for a in active} | {a["action_id"] for a in completed}
    unknown = [i for i in briefing["referenced_action_ids"] if i not in known]
    if unknown:
        reasons.append(_reason("없는Action참조", f"BriefingInput에 없는 action_id: {unknown}"))

    # 누락 방지: Action이 있는데 하나도 언급하지 않거나, 확인 필요가 있는데 확인 section이 비면 브리핑 목적 미달
    if active and not briefing["referenced_action_ids"]:
        reasons.append(_reason("구조오류", "active_actions가 있으나 referenced_action_ids가 비어 있음"))
    if briefing_input["needs_confirmation"] and not briefing["sections"]["confirmation_items"].strip():
        reasons.append(_reason("구조오류", "needs_confirmation이 있으나 confirmation_items가 비어 있음"))

    # B/C. 완료 단정 표현은 approved_by_human이 있는 completed_actions가 있을 때만 가능
    claim_text = _COMPLETION_OK.sub("", spoken)
    if _COMPLETION_CLAIM.search(claim_text):
        approved = [c for c in completed if c.get("approved_by_human")]
        if not approved:
            code = "완료후보오표현" if any(a["awaiting_human_confirmation"] for a in active) else "미승인완료"
            reasons.append(_reason(code, "사람 승인(approved_by_human)된 완료 Action이 없는데 완료 단정 표현을 사용함"))

    # D. decisions 차단 (include_in_briefing=false)
    decisions = briefing_input["decisions"]
    if not decisions["include_in_briefing"]:
        norm_spoken = _norm(spoken)
        for item in decisions["items"]:
            if _norm(item["text"]) and _norm(item["text"]) in norm_spoken:
                reasons.append(_reason("decisions사용", "decisions.items 내용을 브리핑에 사용함"))
                break

    # E. 미결사항·논의를 결정으로 표현 (이 입력에는 사용 가능한 결정이 없다)
    if _DECISION_CLAIM.search(spoken):
        reasons.append(_reason("미결을결정으로", "결정/확정 표현은 BriefingInput에서 허용되지 않음"))

    # F. overdue 표현은 overdue=true Action이 있을 때만
    if _OVERDUE_CLAIM.search(spoken) and not any(a["overdue"] for a in active):
        reasons.append(_reason("기한초과오표현", "overdue=true인 Action이 없는데 기한 초과를 표현함"))

    # I. 진행 중은 🟠 진행중 Action이 있을 때만
    if _IN_PROGRESS_CLAIM.search(spoken) and not any(a["status"] == "진행중" for a in active):
        reasons.append(_reason("상태왜곡", "진행중 Action이 없는데 '진행 중'이라고 표현함"))

    # G. 입력에 없는 사람 이름(담당자 추측·변경)
    blob = _all_text(briefing_input)
    for name in _PERSON.findall(spoken):
        if name.replace(" ", "") not in blob.replace(" ", ""):
            reasons.append(_reason("담당자오류", f"BriefingInput에 없는 이름: {name}"))
            break

    # H. 입력에 없는 날짜(기한 추측·계산)
    allowed = _allowed_month_days(briefing_input)
    for m, d in _MD.findall(spoken):
        if (int(m), int(d)) not in allowed:
            reasons.append(_reason("기한오류", f"BriefingInput에 없는 날짜: {m}월 {d}일"))
            break
    for iso in _ISO.findall(spoken):
        if iso not in blob:
            reasons.append(_reason("기한오류", f"BriefingInput에 없는 날짜: {iso}"))
            break
    return reasons


# ---------------------------------------------------------------- Reviewer

def build_reviewer_prompt(briefing_input: Mapping, briefing: Mapping) -> str:
    agent = BRIEFING_REVIEWER_DEFINITION.read_text(encoding="utf-8")
    return (
        f"{agent}\n\n## BriefingInput\n{json.dumps(briefing_input, ensure_ascii=False, indent=1)}\n\n"
        f"## Briefing (검증 대상)\n{json.dumps(briefing, ensure_ascii=False, indent=1)}\n\n"
        "수정하지 말고 PASS/REJECT 판정 JSON 하나만 출력하라."
    )


def validate_verdict(verdict) -> None:
    """Reviewer 출력 계약: {verdict: PASS|REJECT, reasons: [{code, detail}]}, 추가 필드 금지, REJECT는 사유 필수."""
    if not isinstance(verdict, dict) or set(verdict) != {"verdict", "reasons"}:
        raise BriefingReviewerOutputError("Reviewer 출력은 verdict·reasons 두 필드만 가져야 한다")
    if verdict["verdict"] not in ("PASS", "REJECT"):
        raise BriefingReviewerOutputError(f"verdict는 PASS/REJECT여야 한다: {verdict['verdict']!r}")
    reasons = verdict["reasons"]
    if not isinstance(reasons, list):
        raise BriefingReviewerOutputError("reasons는 배열이어야 한다")
    for r in reasons:
        if not isinstance(r, dict) or set(r) != {"code", "detail"} or r["code"] not in REASON_CODES \
                or not isinstance(r["detail"], str) or not r["detail"].strip():
            raise BriefingReviewerOutputError(f"reason 형식 위반: {r!r}")
    if verdict["verdict"] == "REJECT" and not reasons:
        raise BriefingReviewerOutputError("REJECT는 사유가 1개 이상이어야 한다")


def review_briefing(briefing_input: Mapping, briefing, runner: Callable[[str], Union[str, dict]]) -> dict:
    """코드 검증 → (통과 시) LLM Reviewer. 반환 {verdict, reasons}. briefing·입력은 변경하지 않는다.

    Reviewer 출력이 계약 위반이면 BriefingReviewerOutputError (fail-closed).
    """
    failed = code_check_briefing(briefing_input, briefing)
    if failed:
        return {"verdict": "REJECT", "reasons": failed}
    raw = runner(build_reviewer_prompt(copy.deepcopy(dict(briefing_input)), copy.deepcopy(dict(briefing))))
    try:
        verdict = _parse(raw)
    except ValueError as e:  # JSONDecodeError 포함
        raise BriefingReviewerOutputError(f"Reviewer 출력 거부: {e}") from e
    validate_verdict(verdict)
    return verdict


# ---------------------------------------------------------------- 오케스트레이션

@dataclass(frozen=True)
class BriefingResult:
    approved: bool
    briefing: Optional[dict]            # approved일 때만 값이 있다 (TTS가 읽을 값: briefing["briefing_text"])
    outcome: str                        # approved | rejected | reviewer_invalid
    reasons: tuple = ()                 # 마지막 REJECT 사유 또는 오류 설명
    rejected_draft: Optional[dict] = field(default=None)  # 승인되지 않은 마지막 초안(참고용, 정상 결과 아님)
    generator_calls: int = 0
    reviewer_calls: int = 0


def generate_briefing(
    briefing_input: Mapping,
    generator_runner: Callable[[str], Union[str, dict]],
    reviewer_runner: Callable[[str], Union[str, dict]],
) -> BriefingResult:
    """생성 → 검증 → REJECT면 사유를 참고정보로 1회 재생성 → 재검증. 승인되지 않으면 fail-closed.

    상한: 생성 2회, LLM Reviewer 2회(코드 REJECT는 LLM을 호출하지 않으므로 더 적을 수 있다).
    """
    source = copy.deepcopy(dict(briefing_input))  # 호출자 입력 보호
    gen_calls = rev_calls = 0
    retry = None
    draft = None
    reasons: list = []
    for _attempt in range(MAX_RETRIES + 1):
        gen_calls += 1
        raw = generator_runner(build_briefing_prompt(source, retry))
        try:
            draft = _parse(raw)
        except ValueError:
            draft = None
            reasons = [_reason("구조오류", "생성 출력이 JSON object가 아님")]
            retry = {"previous_briefing": None, "reviewer_reasons": reasons}
            continue
        code_failed = code_check_briefing(source, draft)
        if code_failed:
            verdict = {"verdict": "REJECT", "reasons": code_failed}
        else:
            rev_calls += 1
            try:
                verdict = review_briefing(source, draft, reviewer_runner)
            except BriefingReviewerOutputError as e:
                return BriefingResult(False, None, "reviewer_invalid", (_reason("구조오류", str(e)),),
                                      draft, gen_calls, rev_calls)
        if verdict["verdict"] == "PASS":
            return BriefingResult(True, draft, "approved", (), None, gen_calls, rev_calls)
        reasons = verdict["reasons"]
        retry = {"previous_briefing": draft, "reviewer_reasons": reasons}
    return BriefingResult(False, None, "rejected", tuple(reasons), draft, gen_calls, rev_calls)
