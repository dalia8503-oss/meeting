"""STT 원시 세그먼트 → Transcript(계약: transcript.schema.json) 변환·검증·저장 (T-02-2/3/5).

원칙:
- 텍스트는 가공하지 않는다 (앞뒤 공백 제거만). 요약·보정·추가 없음.
- 화자는 `speaker_N` 라벨만 받는다. 실명·직책 같은 라벨은 STT의 추측이므로 거부한다.
  실명은 사람이 준 speaker_map이 있을 때만 적용하고, 없으면 라벨을 유지한다.
- `low_confidence`는 만들지 않는다 (Gemini Audio에는 신뢰도 지표가 없다. 가짜 confidence 금지).
- STT가 준 timestamp는 정확성을 보장하지 않는다. 형식·시간순만 검증한다.
- 원본 Transcript는 배타적 생성만 한다 (덮어쓰기 금지).
"""
import json
import re
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from meeting_action_agent.contracts import validate
from meeting_action_agent.ingest import safe_meeting_id


class TranscriptError(ValueError):
    """STT 결과가 Transcript 규칙을 어김."""


_TS = re.compile(r"^(\d{1,3}):([0-5]?\d)(?::([0-5]?\d))?$")
_LABEL = re.compile(r"^speaker[\s_\-]*(\d+)$", re.IGNORECASE)


def normalize_timestamp(value) -> str:
    """`MM:SS`·`H:MM:SS`·`HH:MM:SS` → `HH:MM:SS` (녹음 시작 기준 경과시간)."""
    m = _TS.match(value.strip()) if isinstance(value, str) else None
    if not m:
        raise TranscriptError(f"timestamp 형식 오류: {value!r}")
    a, b, c = m.groups()
    if c is None:  # MM:SS (MM은 60 이상 가능)
        total = int(a) * 60 + int(b)
    else:
        if int(a) > 99:
            raise TranscriptError(f"timestamp 범위 오류: {value!r}")
        total = int(a) * 3600 + int(b) * 60 + int(c)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def normalize_speaker(label) -> str:
    m = _LABEL.match(label.strip()) if isinstance(label, str) else None
    if not m:
        raise TranscriptError(f"speaker_N 형식이 아닌 화자 라벨입니다 (이름 추측 금지): {label!r}")
    return f"speaker_{int(m.group(1))}"


def _apply_map(label: str, speaker_map: Optional[Mapping]) -> str:
    for key, value in (speaker_map or {}).items():
        try:
            same = normalize_speaker(key) == label
        except TranscriptError:
            same = key == label
        if same:
            return value["name"]
    return label


def build_transcript(segments: Sequence[Mapping], meeting_id: str, speaker_map: Optional[Mapping] = None) -> List[dict]:
    safe_meeting_id(meeting_id)
    if not segments:
        raise TranscriptError("STT 결과에 발언이 없습니다")
    out, prev = [], None
    for i, s in enumerate(segments):
        if not isinstance(s, Mapping) or not {"speaker", "timestamp", "text"} <= set(s):
            raise TranscriptError(f"세그먼트 {i}: speaker·timestamp·text가 필요합니다")
        text = s["text"].strip() if isinstance(s["text"], str) else ""
        if not text:
            raise TranscriptError(f"세그먼트 {i}: 빈 text")
        ts = normalize_timestamp(s["timestamp"])
        if prev is not None and ts < prev:  # 같은 길이 HH:MM:SS 문자열이라 사전순 = 시간순
            raise TranscriptError(f"세그먼트 {i}: timestamp가 시간순이 아닙니다 ({prev} → {ts})")
        prev = ts
        label = normalize_speaker(s["speaker"])
        out.append({"meeting_id": meeting_id, "speaker": _apply_map(label, speaker_map), "timestamp": ts, "text": text})
    validate("transcript", out)
    return out


def save_original_transcript(transcript: Sequence[Mapping], root) -> Path:
    """`<root>/transcripts/original/<meeting_id>.json`에 신규 생성만 한다 (기존 파일이면 FileExistsError)."""
    validate("transcript", list(transcript))
    ids = {u["meeting_id"] for u in transcript}
    if len(ids) != 1:
        raise TranscriptError(f"한 Transcript의 meeting_id가 하나가 아닙니다: {sorted(ids)}")
    if any(b["timestamp"] < a["timestamp"] for a, b in zip(transcript, transcript[1:])):
        raise TranscriptError("timestamp가 시간순이 아닙니다")
    path = Path(root) / "transcripts" / "original" / f"{safe_meeting_id(next(iter(ids)))}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:  # 배타적 생성
        json.dump(list(transcript), f, ensure_ascii=False, indent=2)
    return path
