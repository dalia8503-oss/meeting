import datetime
import hashlib
import os
import tempfile
from pathlib import Path

from ..ingest import IngestError, ingest_audio
from .mode import get_data_root, get_mode
from .schemas import HealthResponse, MeetingCreated

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 1 << 20


class ApiError(Exception):
    """사용자에게 보여도 안전한 오류. 내부 예외 메시지·경로는 담지 않는다."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def get_health() -> HealthResponse:
    # 키 값은 반환하지 않고 설정 여부(boolean)만 낸다.
    return HealthResponse(
        status="ok",
        mode=get_mode(),
        gemini_configured=bool(os.environ.get("GEMINI_API_KEY")),
    )


def create_meeting(title, meeting_date, filename, stream) -> MeetingCreated:
    """폼 검증 → 임시 파일 → 기존 ingest_audio(형식·signature·길이·create-only·해시) 호출."""
    title = (title or "").strip()
    if not title:
        raise ApiError(422, "missing_title", "회의명을 입력해 주세요.")
    meeting_date = (meeting_date or "").strip()
    if not meeting_date:
        raise ApiError(422, "missing_meeting_date", "회의일을 입력해 주세요.")
    try:
        datetime.date.fromisoformat(meeting_date)
    except ValueError:
        raise ApiError(422, "invalid_meeting_date", "회의일 형식이 올바르지 않습니다.") from None
    if stream is None or not filename:
        raise ApiError(422, "missing_file", "회의 녹음 파일을 선택해 주세요.")

    suffix = Path(filename.replace("\\", "/")).suffix.lower()
    root = get_data_root()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"upload{suffix}"
        h, size = hashlib.sha256(), 0
        with open(src, "wb") as out:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "file_too_large", "파일이 너무 큽니다.")
                h.update(chunk)
                out.write(chunk)
        # 같은 날짜 + 같은 녹음(해시)이면 같은 id → 중복은 create-only로 거부된다.
        meeting_id = f"{meeting_date.replace('-', '')}-{h.hexdigest()[:8]}"
        meta = {"meeting_id": meeting_id, "meeting_date": meeting_date, "title": title}
        try:
            ingest_audio(src, meta, root)
        except FileExistsError:
            raise ApiError(409, "duplicate_meeting", "이미 등록된 회의입니다.") from None
        except IngestError:
            raise ApiError(422, "invalid_audio",
                           "지원하지 않거나 손상된 파일입니다. MP3 / WAV / M4A(최대 2시간)만 가능합니다.") from None
    return MeetingCreated(meeting_id=meeting_id, title=title, meeting_date=meeting_date,
                          audio_filename=Path(filename.replace("\\", "/")).name, status="uploaded")
