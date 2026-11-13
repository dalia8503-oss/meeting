"""사용자 확인 기반 화자 매핑 (T-LIVE-SPK) — 순수 함수만 둔다.

원칙
- STT가 준 `speaker_N` 라벨은 원본이다. 이름으로 덮어쓰지 않는다. 매핑은 회의별 metadata(speaker_map)로만 존재한다.
- 이름은 사람이 명시적으로 확인한 것만 쓴다. 발언 내용·직책·이전 회의·LLM으로 화자 이름을 추측하지 않는다.
- speaker_map 계약: {"speaker_1": {"name": "팀장"}} 에 선택 필드(department, section, display_name, confirmed_by_human)를 더한다.
  identity(누구인가)는 name이다. 부/과(조직정보)와 display_name은 표시·향후 조직별 조회용 구조화 메타데이터이며
  Action 담당자·완료 후보 판단에는 쓰지 않는다 (판단은 사람이 확인한 name만 사용).
"""
import re
from typing import Mapping, Optional, Sequence

_LABEL = re.compile(r"^speaker_\d+$")
SAMPLE_COUNT = 3
MAX_ORG_LEN = 30


class SpeakerMapError(ValueError):
    """사용자가 보낸 speaker_map이 규칙을 어김 (저장하지 않는다)."""


def confirmed_names(speaker_map: Optional[Mapping]) -> dict:
    """계약 형태({"speaker_1": {"name": ..}}) 또는 {"speaker_1": ".."} → {label: name}. 이름이 빈 항목은 미확인으로 본다."""
    out: dict = {}
    for label, value in (speaker_map or {}).items():
        name = value.get("name") if isinstance(value, Mapping) else value
        if isinstance(name, str) and name.strip():
            out[label] = name.strip()
    return out


def _clean(value) -> Optional[str]:
    return re.sub(r"\s+", " ", value).strip() or None if isinstance(value, str) else None


def build_profile(name: str, department=None, section=None) -> dict:
    """사람이 확인한 화자 프로필. 부/과/이름을 구조화해 저장하고 display_name은 파생 값이다."""
    dept, sect = _clean(department), _clean(section)
    profile = {"name": name}
    if dept:
        profile["department"] = dept
    if sect:
        profile["section"] = sect
    profile["display_name"] = " ".join(x for x in (dept, sect, name) if x)
    profile["confirmed_by_human"] = True
    return profile


def confirmed_profiles(speaker_map: Optional[Mapping]) -> dict:
    """{label: {name, department|None, section|None, display_name}}. 이름이 있는 항목만 (구버전 항목은 display_name=name)."""
    out: dict = {}
    for label, name in confirmed_names(speaker_map).items():
        raw = (speaker_map or {}).get(label)
        raw = raw if isinstance(raw, Mapping) else {}
        dept, sect = _clean(raw.get("department")), _clean(raw.get("section"))
        out[label] = {"name": name, "department": dept, "section": sect,
                      "display_name": " ".join(x for x in (dept, sect, name) if x)}
    return out


def display_for_person(text, profiles: Mapping) -> Optional[str]:
    """Action 담당자·지시자 같은 이름 문자열이 확인된 화자 name과 정확히 같을 때만 그 display_name을 돌려준다.
    같은 이름이 서로 다른 표시명으로 확인돼 모호하거나 일치하는 화자가 없으면 None (추측하지 않는다)."""
    if not isinstance(text, str) or not text.strip():
        return None
    key = re.sub(r"\s+", "", text)
    found = {p["display_name"] for p in profiles.values() if re.sub(r"\s+", "", p["name"]) == key}
    return found.pop() if len(found) == 1 else None


def list_speakers(transcript: Sequence[Mapping]) -> list:
    """transcript에서 발견된 speaker_N 목록과 대표 발언(실제 발언 원문, 가장 긴 것부터 최대 3개를 시간순으로).
    요약·생성 없음. 반환: [{"speaker", "utterance_count", "samples": [{"timestamp", "text"}]}] (speaker_N 번호순)."""
    by: dict = {}
    for u in transcript:
        by.setdefault(u["speaker"], []).append(u)
    out = []
    for label in sorted(by, key=lambda s: (int(s.split("_")[1]) if _LABEL.match(s) else 10**9, s)):
        items = by[label]
        picked = sorted(sorted(items, key=lambda u: -len(u["text"]))[:SAMPLE_COUNT], key=lambda u: u["timestamp"])
        out.append({"speaker": label, "utterance_count": len(items),
                    "samples": [{"timestamp": u["timestamp"], "text": u["text"]} for u in picked]})
    return out


def validate_speaker_map(raw: Mapping, transcript: Sequence[Mapping]) -> dict:
    """요청 {label: name | {name, department?, section?} | None} 검증 → 저장용 계약 형태. None은 '미확인'(저장하지 않음).
    존재하지 않는 화자·빈 이름·이름 없는 조직정보는 거부한다. 같은 이름을 여러 화자에 지정하는 것은 허용한다(diarization 분할)."""
    known = {u["speaker"] for u in transcript}
    out: dict = {}
    for label, name in (raw or {}).items():
        if label not in known or not _LABEL.match(label):
            raise SpeakerMapError(f"transcript에 없는 화자입니다: {label}")
        if name is None:
            continue
        dept = sect = None
        if isinstance(name, Mapping):
            dept, sect, name = name.get("department"), name.get("section"), name.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SpeakerMapError(f"{label}: 이름이 비어 있습니다. 모르면 미확인으로 두세요.")
        if any(isinstance(v, str) and len(v.strip()) > MAX_ORG_LEN for v in (dept, sect)):
            raise SpeakerMapError(f"{label}: 부/과 이름이 너무 깁니다.")
        out[label] = build_profile(name.strip(), dept, sect)
    return out


def speaker_context_text(speaker_map: Optional[Mapping]) -> str:
    """Agent prompt에 넣는 사용자 확인 화자 안내. 매핑이 없으면 빈 문자열."""
    names = confirmed_names(speaker_map)
    if not names:
        return ""
    import json
    return (
        "\n## 사용자 확인 화자 (speaker_map)\n"
        "아래는 사람이 직접 확인한 화자 이름이다. 여기에 없는 speaker_N은 미확인이며 누구인지 추측하지 않는다.\n"
        "이 정보는 기존 근거 규칙을 대체하지 않는다. evidence의 speaker는 계속 transcript의 speaker_N 원문을 쓴다.\n"
        + json.dumps(names, ensure_ascii=False) + "\n"
    )
