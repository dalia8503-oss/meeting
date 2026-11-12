"""사용자 확인 기반 화자 매핑 (T-LIVE-SPK) — 순수 함수만 둔다.

원칙
- STT가 준 `speaker_N` 라벨은 원본이다. 이름으로 덮어쓰지 않는다. 매핑은 회의별 metadata(speaker_map)로만 존재한다.
- 이름은 사람이 명시적으로 확인한 것만 쓴다. 발언 내용·직책·이전 회의·LLM으로 화자 이름을 추측하지 않는다.
- speaker_map 계약은 기존 meeting-metadata 그대로다: {"speaker_1": {"name": "팀장"}}.
"""
import re
from typing import Mapping, Optional, Sequence

_LABEL = re.compile(r"^speaker_\d+$")
SAMPLE_COUNT = 3


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
    """요청 {label: name|None} 검증 → 저장용 계약 형태. None은 '미확인'(저장하지 않음).
    존재하지 않는 화자·빈 이름은 거부한다. 같은 이름을 여러 화자에 지정하는 것은 허용한다(diarization 분할)."""
    known = {u["speaker"] for u in transcript}
    out: dict = {}
    for label, name in (raw or {}).items():
        if label not in known:
            raise SpeakerMapError(f"transcript에 없는 화자입니다: {label}")
        if name is None:
            continue
        if not isinstance(name, str) or not name.strip():
            raise SpeakerMapError(f"{label}: 이름이 비어 있습니다. 모르면 미확인으로 두세요.")
        out[label] = {"name": name.strip()}
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
