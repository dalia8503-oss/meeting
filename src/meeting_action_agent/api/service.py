import datetime
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from ..analyst import AnalysisValidationError, save_analysis
from ..audio_pipeline import run_audio_pipeline
from ..ingest import IngestError, ingest_audio, safe_meeting_id
from ..reviewer import ReviewOutputError
from ..stt import SttError, TranscriptError
from ..store import ActionStore
from .mode import get_data_root, get_mode
from .schemas import AnalysisResult, HealthResponse, MeetingCreated

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


# ---------------------------------------------------------------- analyze (orchestration only)

_running: set = set()
_running_lock = threading.Lock()


def _paths(meeting_id: str):
    try:
        mid = safe_meeting_id(meeting_id)
    except ValueError:
        raise ApiError(404, "meeting_not_found", "회의를 찾을 수 없습니다.") from None
    root = get_data_root()
    meetings = root / "meetings"
    return mid, root, meetings / f"{mid}.meta.json", meetings / f"{mid}.analysis.json", meetings / f"{mid}.review.json"


def _live_dependencies():
    if not os.environ.get("GEMINI_API_KEY"):
        raise ApiError(503, "gemini_not_configured", "LIVE 모드에 필요한 Gemini API 키가 서버에 설정되어 있지 않습니다.")
    from ..audio_pipeline import _gemini_json_runner
    from ..llm import gemini_runner
    from ..stt.gemini_stt import GeminiSttProvider

    return dict(provider=GeminiSttProvider(), analyst_runner=gemini_runner,
                reviewer_runner=_gemini_json_runner, speaker_map=None)


def _demo_dependencies(meeting_id: str):
    from . import demo

    return dict(provider=demo.demo_stt_provider(), analyst_runner=demo.make_demo_analyst(meeting_id),
                reviewer_runner=demo.demo_reviewer, speaker_map=demo.DEMO_SPEAKER_MAP)


def _build_result(mid: str, root: Path, analysis_path: Path, review_path: Path) -> AnalysisResult:
    """저장된 산출물(분석·검증·Action)을 화면용 DTO로 옮긴다. 값은 수정하지 않는다."""
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    transcript = json.loads((root / "transcripts" / "original" / f"{mid}.json").read_text(encoding="utf-8"))
    speakers = {u["timestamp"]: u["speaker"] for u in transcript}
    actions = [a for a in ActionStore(root / "actions" / "actions.json").load_all() if a["meeting_id"] == mid]
    return AnalysisResult(
        meeting_id=mid, status="analyzed", mode=get_mode(), utterance_count=len(transcript),
        summary=analysis["key_discussions"], decisions=analysis["decisions"],
        open_issues=analysis["open_issues"], needs_confirmation=analysis["needs_confirmation"],
        actions=[{
            "action_id": a["action_id"], "status": a["status"], "assignee": a["assignee"], "assigner": a["assigner"],
            "task": a["task"], "due_date": a["due_date"], "due_date_normalized": a["due_date_normalized"],
            "flags": a["flags"],
            "evidence": {"timestamp": a["timestamp"], "speaker": speakers.get(a["timestamp"]), "text": a["evidence"]},
        } for a in actions],
        rejected=[{"task": c["task"], "assignee": c["assignee"], "timestamp": c["timestamp"], "evidence": c["evidence"]}
                  for c in review["rejected"]],
    )


def get_result(meeting_id: str) -> AnalysisResult:
    mid, root, meta, analysis_path, review_path = _paths(meeting_id)
    if not meta.is_file():
        raise ApiError(404, "meeting_not_found", "회의를 찾을 수 없습니다.")
    if not (analysis_path.is_file() and review_path.is_file()):
        raise ApiError(404, "not_analyzed", "아직 분석되지 않은 회의입니다.")
    return _build_result(mid, root, analysis_path, review_path)


def analyze_meeting(meeting_id: str) -> AnalysisResult:
    """기존 Audio→Action 경로(audio_pipeline.run_audio_pipeline)를 호출만 한다. 판정·추론·수정은 하지 않는다."""
    mid, root, meta, analysis_path, review_path = _paths(meeting_id)
    if not meta.is_file():
        raise ApiError(404, "meeting_not_found", "회의를 찾을 수 없습니다.")
    if analysis_path.exists():  # create-only: 재분석으로 기존 결과를 덮어쓰지 않는다.
        raise ApiError(409, "already_analyzed", "이미 분석된 회의입니다.")
    with _running_lock:
        if mid in _running:
            raise ApiError(409, "analysis_in_progress", "이 회의는 분석이 진행 중입니다.")
        _running.add(mid)
    try:
        deps = _demo_dependencies(mid) if get_mode() == "demo" else _live_dependencies()
        metadata = json.loads(meta.read_text(encoding="utf-8"))
        try:
            result = run_audio_pipeline(
                None, meeting_id=mid, meeting_date=metadata["meeting_date"], root=root,
                store_path=root / "actions" / "actions.json", title=metadata.get("title"), **deps)
        except (SttError, TranscriptError):
            raise ApiError(502, "stt_failed", "음성 변환(STT)에 실패했습니다. 잠시 후 다시 시도해 주세요.") from None
        except AnalysisValidationError:
            raise ApiError(502, "analysis_invalid", "회의 분석 결과가 검증을 통과하지 못했습니다. 다시 시도해 주세요.") from None
        except ReviewOutputError:
            raise ApiError(502, "review_failed", "Reviewer 검증에 실패했습니다. 다시 시도해 주세요.") from None
        except RuntimeError as e:
            if "해시" in str(e):
                raise ApiError(409, "audio_integrity_error", "원본 녹음이 변경된 것으로 보여 분석을 중단했습니다.") from None
            raise
        # 검증을 통과한 분석·검증 결과를 create-only로 저장한다 (원본 Audio·Transcript는 건드리지 않는다).
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with open(review_path, "x", encoding="utf-8") as f:
            json.dump({"reviews": result["reviews"], "rejected": result["rejected"]}, f, ensure_ascii=False, indent=2)
        transcript = json.loads(Path(result["transcript"]).read_text(encoding="utf-8"))
        save_analysis(result["analysis"], transcript, root / "meetings")
        return _build_result(mid, root, analysis_path, review_path)
    finally:
        with _running_lock:
            _running.discard(mid)
