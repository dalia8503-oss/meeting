"""기한 값 규칙·환산 (T-03-9, SPEC 5.4, R-06).

원문(`due_date`)은 항상 보존하고, `due_date_normalized`는 이 모듈이 규칙으로만 계산한다 (LLM 금지).
환산 대상 (MVP, CHK019 결정):
  - 요일 단독: 회의일 이후 처음 오는 해당 요일 (회의일과 같은 요일은 null)
  - 이번주/다음주 + 요일: 월~일 주 기준 해당 주의 그 요일 (이번주는 지난 요일도 문자 그대로)
  - 연도 없는 월/일: 회의일의 연도 사용, 회의일보다 과거면 null (다음 해 이월·보정 없음)
그 외 표현(범위, "다음 회의까지", "N일 후" 등)은 null. 공휴일·영업일 계산 없음.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional

PENDING = "미정"
NEEDS_CONFIRMATION = "확인 필요"  # common.schema.json 의 NeedsConfirmation 과 동일

_WEEKDAYS = "월화수목금토일"  # date.weekday() 인덱스와 일치
_UNINTERPRETABLE = frozenset({"다음번", "조만간", "빨리"})  # 닫힌 목록 (SPEC 5.4)

# 시각·조사 표현은 원문에만 남기고 환산 판단에서는 제거한다.
_NOISE_RE = re.compile(r"까지|오전|오후|\d{1,2}시(\d{1,2}분)?|\s+")
_WEEKDAY_ONLY_RE = re.compile(rf"^([{_WEEKDAYS}])요일$")
_WEEK_WEEKDAY_RE = re.compile(rf"^(이번|다음)주([{_WEEKDAYS}])요일$")  # 공백은 _NOISE_RE가 제거
_MONTH_DAY_RE = re.compile(r"^(\d{1,2})(?:/|월)(\d{1,2})일?$")


def restore_due_raw(llm_due: Optional[str], utterance_text: str) -> Optional[str]:
    """LLM이 '까지'를 떼고 준 기한 문자열을 근거 발언의 원문 표현으로 복원한다 (정규화 아님).

    발언에서 llm_due가 정확히 한 번 나오고 바로 뒤에 '까지'가 붙어 있을 때만 '까지'를 되살린다.
    발언에 없거나, 여러 번 나오거나, 다른 조사면 llm_due를 그대로 돌려준다. 새 표현은 만들지 않는다.
    """
    if llm_due is None or not llm_due.strip():
        return llm_due
    due = llm_due.strip()
    if due in (PENDING, NEEDS_CONFIRMATION) or due.endswith("까지"):
        return llm_due
    pattern = r"\s+".join(re.escape(tok) for tok in due.split())
    matches = list(re.finditer(pattern, utterance_text))
    if len(matches) != 1:
        return llm_due
    m = matches[0]
    if utterance_text.startswith("까지", m.end()):
        return utterance_text[m.start():m.end() + 2]
    return llm_due


class DueDateBasis(str, Enum):
    NORMALIZED = "normalized"            # 규칙으로 환산됨
    UNNORMALIZED = "unnormalized"        # 원문 보존, 환산 규칙 없음/불가 (null)
    EXPLICIT_PENDING = "explicit_pending"
    NOT_MENTIONED = "not_mentioned"      # 언급 없음
    UNINTERPRETABLE = "uninterpretable"  # 해석 불가 표현


@dataclass(frozen=True)
class DueDateDecision:
    due_date: str                         # 원문 보존 / "미정" / "확인 필요"
    due_date_normalized: Optional[str]    # YYYY-MM-DD 또는 None
    basis: DueDateBasis


def decide_due_date(*, raw: Optional[str], meeting_date: str) -> DueDateDecision:
    """기한 원문 -> (due_date, due_date_normalized). 순수 함수.

    raw:          추출기가 넘긴 기한 원문 (None/공백 = 언급 없음)
    meeting_date: 회의일 YYYY-MM-DD (환산 기준)
    """
    if raw is None or not raw.strip():
        return DueDateDecision(NEEDS_CONFIRMATION, None, DueDateBasis.NOT_MENTIONED)
    text = raw.strip()
    if text == PENDING:
        return DueDateDecision(PENDING, None, DueDateBasis.EXPLICIT_PENDING)
    if text == NEEDS_CONFIRMATION or text in _UNINTERPRETABLE:
        return DueDateDecision(NEEDS_CONFIRMATION, None, DueDateBasis.UNINTERPRETABLE)

    base = date.fromisoformat(meeting_date)
    core = _NOISE_RE.sub("", text)

    m = _WEEKDAY_ONLY_RE.match(core)
    if m:
        delta = (_WEEKDAYS.index(m.group(1)) - base.weekday()) % 7
        if delta:  # 0 = 회의일과 같은 요일 -> 환산하지 않음
            return DueDateDecision(raw, (base + timedelta(days=delta)).isoformat(), DueDateBasis.NORMALIZED)
        return DueDateDecision(raw, None, DueDateBasis.UNNORMALIZED)

    m = _WEEK_WEEKDAY_RE.match(core)
    if m:  # 주 기준 월~일. 이번주는 이미 지난 요일이어도 문자 그대로(다음주로 넘기지 않음)
        monday = base - timedelta(days=base.weekday())
        if m.group(1) == "다음":
            monday += timedelta(days=7)
        target = monday + timedelta(days=_WEEKDAYS.index(m.group(2)))
        return DueDateDecision(raw, target.isoformat(), DueDateBasis.NORMALIZED)

    m = _MONTH_DAY_RE.match(core)
    if m:
        try:
            target = date(base.year, int(m.group(1)), int(m.group(2)))
        except ValueError:  # 존재하지 않는 날짜
            return DueDateDecision(raw, None, DueDateBasis.UNNORMALIZED)
        if target >= base:
            return DueDateDecision(raw, target.isoformat(), DueDateBasis.NORMALIZED)
        return DueDateDecision(raw, None, DueDateBasis.UNNORMALIZED)  # 과거: 이월 금지

    return DueDateDecision(raw, None, DueDateBasis.UNNORMALIZED)
