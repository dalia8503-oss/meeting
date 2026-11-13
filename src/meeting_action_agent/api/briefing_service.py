"""T-BRIEF-API+TTS: 지난 회의 브리핑 생성(BriefingInput → Agent → Reviewer → TTS)과 저장/재사용.

- 직전 회의(previous_meeting_id)와 기준 날짜(current_date)는 호출자가 명시한다. 서버가 추측하지 않는다.
- TTS는 Reviewer PASS로 승인된 briefing_text에만, 그 텍스트를 수정하지 않고 실행한다. REJECT/오류면 TTS 0회.
- 승인된 브리핑은 <data_root>/briefings/{briefing_id}.json(+.wav)에 저장한다. 같은 briefing_id의 재요청은
  저장본을 재사용하며 LLM을 다시 호출하지 않는다(regenerate=true일 때만 새로 생성). briefing_id별 lock으로 중복 실행을 막는다.
- TTS만 실패하면 승인 텍스트는 저장해 두고(audio=null), 재요청 시 LLM 없이 TTS만 다시 시도한다.
- 로그에는 호출 횟수·결과 코드만 남긴다(텍스트·transcript·키 금지).
"""
import json
import logging
import os
import tempfile
import threading
from datetime import date
from pathlib import Path

from ..briefing import BriefingInputError, build_briefing_input
from ..briefing_agent import generate_briefing
from ..ingest import safe_meeting_id
from ..tts import TtsError, WindowsSapiTts
from .mode import get_data_root
from .schemas import BriefingAudio, BriefingResponse
from .service import ApiError, _require_fresh

log = logging.getLogger(__name__)

_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(briefing_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(briefing_id, threading.Lock())


def _runners():
    """(generator_runner, reviewer_runner). 기존 Gemini JSON runner를 쓴다. 테스트에서 교체한다."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise ApiError(503, "gemini_not_configured", "브리핑 생성에 필요한 Gemini API 키가 서버에 설정되어 있지 않습니다.")
    from ..audio_pipeline import _gemini_json_runner

    return _gemini_json_runner, _gemini_json_runner


def _tts_provider():
    """synthesize(text) -> AudioResult 를 가진 로컬 provider. 외부 cloud TTS는 사용하지 않는다."""
    return WindowsSapiTts().synthesize


def make_briefing_id(previous_meeting_id: str, current_date: str) -> str:
    return f"{previous_meeting_id}_{current_date}"


def _dir(root: Path) -> Path:
    return root / "briefings"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load_record(root: Path, briefing_id: str):
    path = _dir(root) / f"{briefing_id}.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec["briefing_id"] == briefing_id and isinstance(rec["briefing_text"], str):
            return rec
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _save_record(root: Path, rec: dict) -> None:
    _atomic_write(_dir(root) / f"{rec['briefing_id']}.json", json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"))


def _response(rec: dict, *, reused: bool) -> BriefingResponse:
    a = rec["audio"]
    return BriefingResponse(
        status="approved", briefing_id=rec["briefing_id"], previous_meeting_id=rec["previous_meeting_id"],
        current_date=rec["current_date"], briefing_text=rec["briefing_text"],
        referenced_action_ids=rec["referenced_action_ids"], reviewer_verdict="PASS",
        audio=BriefingAudio(format="wav", media_type=a["media_type"], duration_sec=a["duration_sec"],
                            size_bytes=a["size_bytes"], url=f"/api/briefings/{rec['briefing_id']}/audio"),
        reused=reused)


def _attach_audio(root: Path, rec: dict) -> dict:
    """승인된 텍스트(rec['briefing_text'])를 그대로 TTS에 넣고 wav를 저장한다."""
    try:
        audio = _tts_provider()(rec["briefing_text"])
    except TtsError as e:
        log.warning("briefing tts failed: %s", e)
        raise ApiError(503, "tts_unavailable",
                       "음성 생성에 실패했습니다. 브리핑 텍스트는 저장되어 있으니 잠시 후 다시 시도해 주세요.") from None
    _atomic_write(_dir(root) / f"{rec['briefing_id']}.wav", audio.data)
    rec = {**rec, "audio": {"media_type": audio.media_type, "duration_sec": round(audio.duration_sec, 3),
                            "size_bytes": len(audio.data)}}
    _save_record(root, rec)
    return rec


def create_briefing(previous_meeting_id: str, current_date: str, regenerate: bool = False) -> BriefingResponse:
    try:
        mid = safe_meeting_id(previous_meeting_id)
    except ValueError:
        raise ApiError(404, "previous_meeting_not_found", "직전 회의를 찾을 수 없습니다.") from None
    try:
        date.fromisoformat(current_date)
    except (TypeError, ValueError):
        raise ApiError(422, "invalid_current_date", "current_date는 YYYY-MM-DD 형식이어야 합니다.") from None

    root = get_data_root()
    _require_fresh(root, mid, "직전 회의의 화자 정보가 변경되어 재분석이 필요합니다. 재분석 후 브리핑해 주세요.")
    briefing_id = make_briefing_id(mid, current_date)
    with _lock_for(briefing_id):  # 같은 브리핑의 동시/중복 요청은 한 번만 생성한다
        rec = None if regenerate else _load_record(root, briefing_id)
        if rec is not None:
            wav = _dir(root) / f"{briefing_id}.wav"
            if rec.get("audio") and wav.is_file():
                return _response(rec, reused=True)
            return _response(_attach_audio(root, rec), reused=True)  # 승인 텍스트 재사용, LLM 없음

        try:
            binput = build_briefing_input(mid, current_date=current_date, data_root=root)
        except BriefingInputError as e:
            if "분석" in str(e):
                raise ApiError(409, "previous_meeting_not_analyzed", "직전 회의가 아직 분석되지 않았습니다.") from None
            raise ApiError(404, "previous_meeting_not_found", "직전 회의를 찾을 수 없습니다.") from None

        generator, reviewer = _runners()
        result = generate_briefing(binput, generator, reviewer)
        log.info("briefing %s: outcome=%s generator_calls=%d reviewer_calls=%d",
                 briefing_id, result.outcome, result.generator_calls, result.reviewer_calls)
        if not result.approved:  # fail closed: rejected draft는 응답·저장·TTS 어디에도 쓰지 않는다
            if result.outcome == "reviewer_invalid":
                raise ApiError(502, "briefing_review_failed", "브리핑 검증 결과를 확인할 수 없어 제공하지 않습니다.")
            raise ApiError(422, "briefing_rejected", "브리핑이 검증을 통과하지 못해 제공하지 않습니다.")

        b = result.briefing
        rec = {"briefing_id": briefing_id, "previous_meeting_id": mid, "current_date": current_date,
               "briefing_text": b["briefing_text"], "referenced_action_ids": list(b["referenced_action_ids"]),
               "reviewer_verdict": "PASS", "audio": None}
        _save_record(root, rec)  # TTS가 실패해도 승인 텍스트는 보존
        return _response(_attach_audio(root, rec), reused=False)


def get_audio_path(briefing_id: str) -> Path:
    try:
        bid = safe_meeting_id(briefing_id)
    except ValueError:
        raise ApiError(404, "audio_not_found", "음성을 찾을 수 없습니다.") from None
    path = _dir(get_data_root()) / f"{bid}.wav"
    if not path.is_file():
        raise ApiError(404, "audio_not_found", "음성을 찾을 수 없습니다.")
    return path
