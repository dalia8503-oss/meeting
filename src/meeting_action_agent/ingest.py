"""T-01-1/T-01-2/T-01-4: 녹음 파일 입력.

책임: 입력 사양 검증(형식·길이) → 메타데이터 계약 검증 → audio/·meetings/에 신규 생성 → 원본 해시 기록.
STT·분석은 하지 않는다. 원본 녹음은 배타적 생성(`open(path, "xb")`)만 하며 덮어쓰지 않는다.

한계(MVP): 길이(최대 2시간) 검증은 WAV만 가능하다. MP3/M4A는 디코더 의존성이 필요해 길이를 모르면
`duration_sec=None`으로 두고 값을 꾸며내지 않는다.
"""
import datetime
import hashlib
import json
import re
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from meeting_action_agent.contracts import validate

SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".m4a")
MAX_DURATION_SEC = 2 * 60 * 60

_SAFE_ID = re.compile(r"^[A-Za-z0-9가-힣][A-Za-z0-9가-힣_.\-]*$")


class IngestError(ValueError):
    """입력 사양(형식·길이·내용) 위반. 오류 메시지를 내고 처리하지 않는다 (US-01)."""


def safe_meeting_id(meeting_id: str) -> str:
    """meeting_id는 파일명에 쓰이므로 경로 구분자·상위 경로를 허용하지 않는다."""
    if not isinstance(meeting_id, str) or not _SAFE_ID.match(meeting_id) or ".." in meeting_id:
        raise ValueError(f"파일명으로 쓸 수 없는 meeting_id: {meeting_id!r}")
    return meeting_id


def _looks_like(ext: str, head: bytes) -> bool:
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    return False


def validate_audio(path, max_duration_sec: float = MAX_DURATION_SEC) -> dict:
    """형식(확장자 + 파일 시그니처)과 길이를 검증한다. 반환: ext, size_bytes, duration_sec(알 수 없으면 None)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise IngestError(f"지원하지 않는 형식입니다: {p.suffix or '(확장자 없음)'} (지원: mp3, wav, m4a)")
    if not p.is_file():
        raise IngestError(f"파일이 없습니다: {p}")
    size = p.stat().st_size
    if size == 0:
        raise IngestError(f"빈 파일입니다: {p.name}")
    with open(p, "rb") as f:
        head = f.read(16)
    if not _looks_like(ext, head):
        raise IngestError(f"파일 내용이 {ext} 형식이 아닙니다: {p.name}")
    duration: Optional[float] = None
    if ext == ".wav":
        try:
            with wave.open(str(p)) as w:
                duration = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError) as e:
            raise IngestError(f"WAV를 읽을 수 없습니다 (PCM WAV만 지원): {e}") from None
        if duration > max_duration_sec:
            raise IngestError(f"최대 2시간을 초과합니다: {duration:.0f}초 (한도 {max_duration_sec:g}초)")
    return {"ext": ext, "size_bytes": size, "duration_sec": duration}


@dataclass(frozen=True)
class IngestResult:
    audio_path: Path
    meta_path: Path
    hash_path: Path
    sha256: str


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_audio(src, metadata: Mapping, root, *, max_duration_sec: float = MAX_DURATION_SEC) -> IngestResult:
    """검증 → `audio/<id><ext>`(신규 생성만), `meetings/<id>.meta.json`, `meetings/<id>.audio.json`(sha256).

    검증이 모두 끝난 뒤에만 파일을 만든다. 같은 meeting_id가 이미 있으면 FileExistsError (덮어쓰기 없음).
    """
    validate("meeting-metadata", metadata)
    mid = safe_meeting_id(metadata["meeting_id"])
    try:
        datetime.date.fromisoformat(metadata["meeting_date"])
    except ValueError:
        raise IngestError(f"존재하지 않는 meeting_date: {metadata['meeting_date']}") from None
    src = Path(src)
    info = validate_audio(src, max_duration_sec)

    root = Path(root)
    audio_path = root / "audio" / f"{mid}{info['ext']}"
    meta_path = root / "meetings" / f"{mid}.meta.json"
    hash_path = root / "meetings" / f"{mid}.audio.json"
    for existing in (audio_path, meta_path, hash_path, *(root / "audio" / f"{mid}{e}" for e in SUPPORTED_EXTENSIONS)):
        if existing.exists():
            raise FileExistsError(f"이미 존재합니다 (덮어쓰지 않음): {existing}")

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(audio_path, "xb") as fout:  # 배타적 생성
        shutil.copyfileobj(fin, fout)
    digest = _sha256(audio_path)
    with open(meta_path, "x", encoding="utf-8") as f:
        json.dump(dict(metadata), f, ensure_ascii=False, indent=2)
    record = {
        "file": f"audio/{audio_path.name}", "sha256": digest, "size_bytes": info["size_bytes"],
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    with open(hash_path, "x", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return IngestResult(audio_path, meta_path, hash_path, digest)


def verify_audio_hash(root, meeting_id: str) -> bool:
    """기록된 해시와 현재 원본 녹음의 해시가 같은지 (사후 변경 탐지, R-08)."""
    root = Path(root)
    record = json.loads((root / "meetings" / f"{safe_meeting_id(meeting_id)}.audio.json").read_text(encoding="utf-8"))
    audio = root / record["file"]
    return audio.is_file() and _sha256(audio) == record["sha256"]
