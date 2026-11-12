import logging
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from . import service
from .schemas import AnalysisResult, HealthResponse, MeetingCreated

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}})


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return service.get_health()


# mode는 받지 않는다 (MAA_MODE 환경변수만 사용). 알 수 없는 폼 필드는 무시된다.
@router.post("/meetings", response_model=MeetingCreated, status_code=201)
def create_meeting(
    title: Optional[str] = Form(None),
    meeting_date: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
):
    try:
        return service.create_meeting(
            title, meeting_date,
            audio_file.filename if audio_file else None,
            audio_file.file if audio_file else None,
        )
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:  # 내부 상세는 서버 로그에만 남기고 응답에는 노출하지 않는다.
        log.exception("meeting upload failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# meeting_id만 URL로 받는다. mode/provider/model은 서버 설정(MAA_MODE, 환경변수)만 따른다.
@router.post("/meetings/{meeting_id}/analyze", response_model=AnalysisResult)
def analyze_meeting(meeting_id: str):
    try:
        return service.analyze_meeting(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("meeting analysis failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/meetings/{meeting_id}/result", response_model=AnalysisResult)
def get_result(meeting_id: str):
    try:
        return service.get_result(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("result lookup failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
