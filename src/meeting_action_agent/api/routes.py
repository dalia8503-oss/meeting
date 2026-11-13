import logging
from typing import Optional

from fastapi import APIRouter, Body, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import briefing_service, service
from .schemas import (
    ActionView, AdoptRequest, AnalysisResult, AnalyzeRequest, BriefingRequest, BriefingResponse, HealthResponse, MeetingCreated, MeetingDeleted, MeetingList, ReviewRequest,
    SpeakerList, SpeakerMapRequest, SpeakerSuggestionList, WithdrawRequest)

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}})


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return service.get_health()


@router.get("/meetings", response_model=MeetingList)
def list_meetings():
    try:
        return MeetingList(meetings=service.list_meetings())
    except Exception:
        log.exception("meeting list failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


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


# 사용자가 명시적으로 누른 회의 삭제(Human management action). Agent는 이 API를 호출하지 않는다.
@router.delete("/meetings/{meeting_id}", response_model=MeetingDeleted)
def delete_meeting(meeting_id: str):
    try:
        return service.delete_meeting(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("meeting delete failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# ---- 화자 확인 (T-LIVE-SPK): STT와 분석 사이의 interaction boundary. 화자 이름은 사람이 입력한 것만 저장한다.
@router.post("/meetings/{meeting_id}/transcribe", response_model=SpeakerList)
def transcribe_meeting(meeting_id: str):
    try:
        return service.transcribe_meeting(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("meeting transcribe failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/meetings/{meeting_id}/speakers", response_model=SpeakerList)
def get_speakers(meeting_id: str):
    try:
        return service.get_speakers(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("speaker lookup failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@router.put("/meetings/{meeting_id}/speaker-map", response_model=SpeakerList)
def put_speaker_map(meeting_id: str, body: SpeakerMapRequest):
    try:
        return service.put_speaker_map(meeting_id, body.speaker_map)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("speaker map save failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# AI 1차 추천 (추정값). 확정 speaker_map은 바꾸지 않는다.
@router.post("/meetings/{meeting_id}/speaker-suggestions", response_model=SpeakerSuggestionList)
def post_speaker_suggestions(meeting_id: str):
    try:
        return service.suggest_speakers(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("speaker suggestion failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# Reviewer가 제외한 후보를 사람이 Action으로 채택한다 (사람의 명시적 결정). Reviewer 판정 기록은 바뀌지 않는다.
@router.post("/meetings/{meeting_id}/rejected/{index}/adopt", response_model=AnalysisResult)
def adopt_rejected(meeting_id: str, index: int, body: AdoptRequest):
    try:
        return service.adopt_rejected(meeting_id, index, body.actor_name, body.assignees, body.assigner)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("rejected adoption failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# 분석 후 화자가 바뀌어 stale이 된 분석을 사용자가 명시적으로 다시 실행한다.
@router.post("/meetings/{meeting_id}/reanalyze", response_model=AnalysisResult)
def reanalyze_meeting(meeting_id: str):
    try:
        return service.reanalyze_meeting(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("meeting reanalysis failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# meeting_id만 URL로 받는다. mode/provider/model은 서버 설정(MAA_MODE, 환경변수)만 따른다.
@router.post("/meetings/{meeting_id}/analyze", response_model=AnalysisResult)
def analyze_meeting(meeting_id: str, body: Optional[AnalyzeRequest] = Body(None)):
    try:
        return service.analyze_meeting(meeting_id, body.previous_meeting_ids if body else None)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("meeting analysis failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# 사람이 분석 결과를 확정한다 (Action 0건이어도 가능). Action·Reviewer 판정은 바뀌지 않는다.
@router.post("/meetings/{meeting_id}/finalize", response_model=AnalysisResult)
def finalize_analysis(meeting_id: str):
    try:
        return service.finalize_analysis(meeting_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("analysis finalize failed")
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


@router.post("/actions/{action_id}/review", response_model=ActionView)
def review_action(action_id: str, body: ReviewRequest):
    try:
        return service.review(action_id, body.choice, body.actor_name, body.meeting_id, body.timestamp)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("action review failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@router.post("/actions/{action_id}/withdraw-completion", response_model=ActionView)
def withdraw_completion(action_id: str, body: WithdrawRequest):
    try:
        return service.withdraw(action_id, body.actor_name)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("completion withdraw failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# ---- 지난 회의 브리핑: 생성(POST)과 재생(GET audio)을 분리한다. 재생은 LLM을 호출하지 않는다.
@router.post("/briefings", response_model=BriefingResponse)
def create_briefing(body: BriefingRequest):
    try:
        return briefing_service.create_briefing(body.previous_meeting_id, body.current_date, body.regenerate)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    except Exception:
        log.exception("briefing failed")
        return _error(500, "internal_error", "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/briefings/{briefing_id}/audio")
def get_briefing_audio(briefing_id: str):
    try:
        path = briefing_service.get_audio_path(briefing_id)
    except service.ApiError as e:
        return _error(e.status, e.code, e.message)
    return FileResponse(path, media_type="audio/wav")
