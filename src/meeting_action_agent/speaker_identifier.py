"""Speaker Identification Agent (화자 1차 추천) — prompt 구성 + 출력 검증. 저장·확정은 하지 않는다.

원칙
- 추천(speaker_suggestions)은 추정값이다. 확정 신원(speaker_map)이 아니며 이 모듈은 speaker_map을 읽거나 쓰지 않는다.
- 이름은 transcript에 실제로 등장한 표기만 인정한다 (Agent가 만든 이름은 코드가 null로 되돌린다).
- 직급/호칭만 있는 이름, 근거(evidence) 없는 이름은 추천하지 않는다.
- 부(department)/과(section)는 녹취에 "1부 3과 이과장"처럼 이름과 붙어 명시된 경우에만 추천한다. 아니면 null (조직정보는 사람이 보완).
- 같은 이름이 여러 화자에 추천되면 근거 충돌로 보고 needs_confirmation으로 낮춘다.
"""
import json
import re
from pathlib import Path
from typing import Callable, Mapping, Sequence

AGENT_DEFINITION = Path(__file__).resolve().parents[2] / ".claude" / "agents" / "speaker-identifier.md"
MAX_EVIDENCE = 5
CONFLICT_CONFIDENCE_CAP = 0.59
TITLES = ("부장", "차장", "과장", "대리", "사원", "주임", "팀장", "실장", "본부장", "이사", "상무", "전무", "대표",
          "사장", "반장", "소장", "팀원", "선생", "매니저", "담당", "책임", "선임", "수석")


class SpeakerSuggestionError(ValueError):
    """Agent 출력이 JSON 계약을 만족하지 못함. 추천으로 쓰지 않는다."""


def confidence_level(confidence: float) -> str:
    """UI 표시용 구간 (숫자를 정밀한 확률처럼 쓰지 않는다). 0.85 이상 높음 / 0.60~0.84 보통 / 그 미만 낮음."""
    if confidence >= 0.85:
        return "high"
    return "medium" if confidence >= 0.60 else "low"


def build_prompt(transcript: Sequence[Mapping], speakers: Sequence[Mapping]) -> str:
    agent = AGENT_DEFINITION.read_text(encoding="utf-8")
    payload = {
        "speakers": [s["speaker"] for s in speakers],
        "representative_utterances": {s["speaker"]: s["samples"] for s in speakers},
        "transcript": [{"speaker": u["speaker"], "timestamp": u["timestamp"], "text": u["text"]} for u in transcript],
    }
    return (f"{agent}\n\n## INPUT\n" + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n\n화자별 추천 JSON 객체 하나만 출력하라.")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_title_only(name: str) -> str:
    rest = name
    for t in sorted(TITLES, key=len, reverse=True):
        rest = rest.replace(t, "")
    return re.sub(r"[님씨\s]", "", rest)


def _usable_name(name, haystack: str):
    """transcript에 실제로 있고, 호칭만이 아닌 이름만 통과시킨다. 아니면 None."""
    if not isinstance(name, str):
        return None
    cleaned = name.strip()
    squashed = _squash(cleaned)
    squashed = squashed[:-1] if squashed.endswith("님") and len(squashed) > 1 else squashed
    if not squashed or not _strip_title_only(squashed):
        return None
    return cleaned if squashed in haystack else None


def _grounded_org(name: str, department, section, haystack: str):
    """부/과가 이름과 붙은 형태("1부 3과 이과장")로 transcript에 실제 있을 때만 인정한다. 아니면 (None, None)."""
    dept = _squash(department) if isinstance(department, str) else ""
    sect = _squash(section) if isinstance(section, str) else ""
    if not (dept or sect):
        return None, None
    squashed = _squash(name)
    squashed = squashed[:-1] if squashed.endswith("님") and len(squashed) > 1 else squashed
    if dept + sect + squashed not in haystack:
        return None, None
    return (department.strip() if dept else None), (section.strip() if sect else None)


def _null(speaker: str) -> dict:
    return {"speaker_id": speaker, "suggested_name": None, "department": None, "section": None,
            "confidence": 0.0, "evidence": [],
            "needs_confirmation": True}


def validate_suggestions(raw, transcript: Sequence[Mapping]) -> list:
    """Agent 출력(JSON 문자열/객체) → 모든 transcript 화자에 대한 검증된 추천 목록 (speaker_N 번호순).
    transcript에 없는 화자는 버리고, 빠진 화자는 null 추천으로 채운다."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise SpeakerSuggestionError("JSON이 아님") from None
    if isinstance(data, Mapping) and isinstance(data.get("suggestions"), list):
        data = {s.get("speaker_id"): s for s in data["suggestions"] if isinstance(s, Mapping)}
    if not isinstance(data, Mapping):
        raise SpeakerSuggestionError("JSON object가 아님")

    labels = sorted({u["speaker"] for u in transcript},
                    key=lambda s: (int(s.split("_")[1]) if re.fullmatch(r"speaker_\d+", s) else 10**9, s))
    haystack = _squash("".join(u["text"] for u in transcript))
    out: dict = {}
    for label in labels:
        item = data.get(label)
        if not isinstance(item, Mapping):
            out[label] = _null(label)
            continue
        name = _usable_name(item.get("suggested_name"), haystack)
        evidence = [e.strip() for e in item.get("evidence") or [] if isinstance(e, str) and e.strip()][:MAX_EVIDENCE]
        conf = item.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
        conf = min(max(conf, 0.0), 1.0)
        if name is None or not evidence or conf <= 0.0:
            out[label] = _null(label)
            continue
        dept, sect = _grounded_org(name, item.get("department"), item.get("section"), haystack)
        out[label] = {"speaker_id": label, "suggested_name": name, "department": dept, "section": sect,
                      "confidence": round(conf, 2),
                      "evidence": evidence, "needs_confirmation": bool(item.get("needs_confirmation", False))}
    # 같은 이름이 여러 화자에 추천되면 근거 충돌 → 확인 필요 (이름은 남기되 신뢰도를 낮춘다)
    by_name: dict = {}
    for s in out.values():
        if s["suggested_name"]:
            by_name.setdefault(_squash(s["suggested_name"]), []).append(s)
    for group in by_name.values():
        if len(group) > 1:
            for s in group:
                s["needs_confirmation"] = True
                s["confidence"] = min(s["confidence"], CONFLICT_CONFIDENCE_CAP)
    return [out[label] for label in labels]


def identify_speakers(transcript: Sequence[Mapping], speakers: Sequence[Mapping],
                      runner: Callable[[str], str]) -> list:
    """runner(prompt) → 검증된 추천 목록. runner 예외는 호출 측이 처리한다. 저장·확정 없음."""
    return validate_suggestions(runner(build_prompt(transcript, speakers)), transcript)
