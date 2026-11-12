import datetime
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from ..analyst import AnalysisValidationError, save_analysis
from ..audio_pipeline import run_audio_pipeline
from ..ingest import IngestError, ingest_audio, safe_meeting_id
from ..review import review_action, withdraw_completion
from ..reviewer import ReviewOutputError
from ..state import CompletionGuardError, TransitionError
from ..stt import SttError, TranscriptError
from ..store import ActionStore
from .mode import get_data_root, get_mode
from .schemas import ActionView, AnalysisResult, HealthResponse, MeetingCreated, MeetingSummary

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


def list_meetings() -> list:
    """저장된 회의 목록(이전 회의 후보 선택용). 저장 구조에서 읽을 수 있는 값만 쓰고 추정하지 않는다.
    정렬: meeting_date 내림차순 → 등록 시각(audio.json recorded_at) 내림차순 → meeting_id."""
    root = get_data_root()
    meetings = root / "meetings"
    if not meetings.is_dir():
        return []
    counts: dict = {}
    for a in ActionStore(root / "actions" / "actions.json").load_all():
        counts[a["meeting_id"]] = counts.get(a["meeting_id"], 0) + 1
    out = []
    for meta_path in meetings.glob("*.meta.json"):
        try:
            meta = _read_json(meta_path)
            mid = safe_meeting_id(meta["meeting_id"])
            if meta_path.name != f"{mid}.meta.json":
                continue
            record = meta_path.with_name(f"{mid}.audio.json")
            created_at = _read_json(record).get("recorded_at") if record.is_file() else None
            analyzed = _run_path(root, mid).is_file() or (
                (meetings / f"{mid}.analysis.json").is_file() and (meetings / f"{mid}.review.json").is_file())
            out.append(MeetingSummary(
                meeting_id=mid, title=meta["title"], meeting_date=meta["meeting_date"], created_at=created_at,
                analysis_status="analyzed" if analyzed else "uploaded", action_count=counts.get(mid, 0)))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # 손상된 항목 하나가 전체 목록을 막지 않게 한다.
    out.sort(key=lambda m: m.meeting_id)
    out.sort(key=lambda m: m.created_at or "", reverse=True)
    out.sort(key=lambda m: m.meeting_date, reverse=True)
    return out


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


def _run_path(root: Path, mid: str) -> Path:
    return root / "meetings" / f"{mid}.run.json"


def _live_dependencies():
    if not os.environ.get("GEMINI_API_KEY"):
        raise ApiError(503, "gemini_not_configured", "LIVE 모드에 필요한 Gemini API 키가 서버에 설정되어 있지 않습니다.")
    from ..audio_pipeline import _gemini_json_runner
    from ..llm import gemini_runner
    from ..stt.gemini_stt import GeminiSttProvider

    return dict(provider=GeminiSttProvider(), analyst_runner=gemini_runner,
                reviewer_runner=_gemini_json_runner, speaker_map=None,
                followup_runner=_gemini_json_runner, followup_reviewer_runner=_gemini_json_runner)


def _demo_dependencies(meeting_id: str):
    from . import demo

    return dict(provider=demo.demo_stt_provider(), analyst_runner=demo.make_demo_analyst(meeting_id),
                reviewer_runner=demo.demo_reviewer, speaker_map=demo.DEMO_SPEAKER_MAP,
                followup_runner=demo.demo_followup_runner, followup_reviewer_runner=demo.demo_followup_reviewer)


def _speakers(root: Path, meeting_id: str, cache: dict) -> dict:
    if meeting_id not in cache:
        path = root / "transcripts" / "original" / f"{meeting_id}.json"
        try:
            cache[meeting_id] = {u["timestamp"]: u["speaker"]
                                 for u in json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError, TypeError):
            cache[meeting_id] = {}
    return cache[meeting_id]


def _action_view(a: dict, root: Path, cache: dict) -> dict:
    """저장된 Action을 화면용 DTO로 옮긴다. 값은 수정하지 않는다."""
    speaker = _speakers(root, a["meeting_id"], cache).get(a["timestamp"])
    return {
        "action_id": a["action_id"], "meeting_id": a["meeting_id"], "status": a["status"],
        "assignee": a["assignee"], "assigner": a["assigner"], "task": a["task"], "due_date": a["due_date"],
        "due_date_normalized": a["due_date_normalized"], "flags": a["flags"],
        "evidence": {"timestamp": a["timestamp"], "speaker": speaker, "text": a["evidence"]},
        "approved_by_human": a.get("approved_by_human"), "history": a["history"],
    }


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _build_result(mid: str, root: Path) -> AnalysisResult:
    """저장된 산출물(분석·검증·실행 기록·Action)을 화면용 DTO로 옮긴다. 값은 수정하지 않는다."""
    meetings = root / "meetings"
    analysis_path, review_path = meetings / f"{mid}.analysis.json", meetings / f"{mid}.review.json"
    run_path = _run_path(root, mid)
    run = _read_json(run_path) if run_path.is_file() else {}
    analysis = _read_json(analysis_path) if analysis_path.is_file() else None
    review = _read_json(review_path) if review_path.is_file() else {"rejected": []}
    transcript = _read_json(root / "transcripts" / "original" / f"{mid}.json")
    all_actions = ActionStore(root / "actions" / "actions.json").load_all()
    cache: dict = {}
    mine = [a for a in all_actions if a["meeting_id"] == mid]
    followed = {r["action_id"] for r in run.get("followup_results", [])}
    shown = mine + [a for a in all_actions if a["action_id"] in followed and a["meeting_id"] != mid]
    return AnalysisResult(
        meeting_id=mid, status="analyzed", mode=get_mode(), utterance_count=len(transcript),
        summary=analysis["key_discussions"] if analysis else [],
        decisions=analysis["decisions"] if analysis else [],
        open_issues=analysis["open_issues"] if analysis else [],
        needs_confirmation=analysis["needs_confirmation"] if analysis else [],
        actions=[_action_view(a, root, cache) for a in shown],
        rejected=[{"task": c["task"], "assignee": c["assignee"], "timestamp": c["timestamp"], "evidence": c["evidence"]}
                  for c in review["rejected"]],
        meeting_analysis=({"summary": analysis["key_discussions"], "decisions": analysis["decisions"],
                           "open_issues": analysis["open_issues"],
                           "needs_confirmation": analysis["needs_confirmation"]} if analysis else None),
        new_actions=[_action_view(a, root, cache) for a in mine],
        followup_results=run.get("followup_results", []),
        followup_invalid=run.get("followup_invalid", []),
        previous_meeting_ids=run.get("previous_meeting_ids", []),
        execution=run.get("execution"),
        new_actions_error=run.get("new_actions_error"),
        followup_error=run.get("followup_error"),
    )


def get_result(meeting_id: str) -> AnalysisResult:
    mid, root, meta, analysis_path, review_path = _paths(meeting_id)
    if not meta.is_file():
        raise ApiError(404, "meeting_not_found", "회의를 찾을 수 없습니다.")
    if not (_run_path(root, mid).is_file() or (analysis_path.is_file() and review_path.is_file())):
        raise ApiError(404, "not_analyzed", "아직 분석되지 않은 회의입니다.")
    return _build_result(mid, root)


def _check_previous_ids(mid: str, ids, meetings: Path) -> list:
    """이전 회의는 사용자가 명시한 것만 쓴다 (자동 추론 없음). 존재하지 않는 회의·자기 자신은 거부."""
    out: list = []
    for raw in ids or []:
        try:
            pid = safe_meeting_id(raw)
        except ValueError:
            raise ApiError(404, "previous_meeting_not_found", "이전 회의를 찾을 수 없습니다.") from None
        if pid == mid:
            raise ApiError(422, "invalid_previous_meeting", "현재 회의를 이전 회의로 지정할 수 없습니다.")
        if not (meetings / f"{pid}.meta.json").is_file():
            raise ApiError(404, "previous_meeting_not_found", "이전 회의를 찾을 수 없습니다.")
        if pid not in out:
            out.append(pid)
    return out


def _execution_record(orch) -> Optional[dict]:
    """발표/Demo용 최소 정보. 시각은 분석 시작 기준 상대 초이며 thread 정보는 노출하지 않는다."""
    if orch is None:
        return None
    base = min(e.started_at for e in orch.execution)
    total: dict = {}
    agents = []
    for e in orch.execution:
        for k, v in e.reviewer.items():
            total[k] = total.get(k, 0) + v
        agents.append({"agent": e.agent, "status": e.status, "started_at": round(e.started_at - base, 4),
                       "finished_at": round(e.finished_at - base, 4), "reviewer": dict(e.reviewer), "error": e.error})
    return {"mode": orch.mode, "overlapped": orch.overlapped, "agents": agents, "reviewer": total}


def _followup_records(orch, store: ActionStore) -> list:
    out = []
    for o in orch.followup_outcomes if orch else ():
        evidence = []
        if o.outcome in ("applied", "reassessed"):  # 방금 반영된 근거 = 해당 Action의 마지막 history 근거
            hist = store.get(o.action_id)["history"][-1]
            evidence = [{"timestamp": q["timestamp"], "speaker": q.get("speaker"), "text": q["text"]}
                        for q in hist.get("evidence", [])]
        out.append({"action_id": o.action_id, "outcome": o.outcome, "from_status": o.from_status,
                    "to_status": o.to_status, "detail": o.detail,
                    "reviewer_verdict": (o.review or {}).get("verdict"), "evidence": evidence})
    return out


def analyze_meeting(meeting_id: str, previous_meeting_ids=None) -> AnalysisResult:
    """기존 Audio→Action 경로(audio_pipeline.run_audio_pipeline)를 호출하고, 1차/2차 판단은 Orchestrator에 맡긴다.
    이 함수는 판정·추론·상태전이를 하지 않는다."""
    mid, root, meta, analysis_path, review_path = _paths(meeting_id)
    if not meta.is_file():
        raise ApiError(404, "meeting_not_found", "회의를 찾을 수 없습니다.")
    previous = _check_previous_ids(mid, previous_meeting_ids, root / "meetings")
    run_path = _run_path(root, mid)
    if analysis_path.exists() or run_path.exists():  # create-only: 재분석으로 기존 결과를 덮어쓰지 않는다.
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
                store_path=root / "actions" / "actions.json", title=metadata.get("title"),
                previous_meeting_ids=previous, **deps)
        except (SttError, TranscriptError):
            raise ApiError(502, "stt_failed", "음성 변환(STT)에 실패했습니다. 잠시 후 다시 시도해 주세요.") from None
        except AnalysisValidationError:
            raise ApiError(502, "analysis_invalid", "회의 분석 결과가 검증을 통과하지 못했습니다. 다시 시도해 주세요.") from None
        except ReviewOutputError:
            raise ApiError(502, "review_failed", "Reviewer 검증에 실패했습니다. 다시 시도해 주세요.") from None
        except CompletionGuardError:  # 안전 위반: fail-closed (성공 응답으로 감싸지 않는다)
            raise ApiError(409, "completion_guard_violation",
                           "Agent가 '완료'를 제안해 분석을 중단했습니다. 완료는 사람만 승인할 수 있습니다.") from None
        except FileExistsError:  # 이행점검 snapshot 충돌: 기존 결과를 덮어쓰지 않는다
            raise ApiError(409, "followup_snapshot_conflict", "이미 이행점검 결과가 저장된 회의입니다.") from None
        except RuntimeError as e:
            if "해시" in str(e):
                raise ApiError(409, "audio_integrity_error", "원본 녹음이 변경된 것으로 보여 분석을 중단했습니다.") from None
            raise
        orch = result.get("orchestration")
        store = ActionStore(root / "actions" / "actions.json")
        run = {
            "previous_meeting_ids": previous,
            "execution": _execution_record(orch),
            "followup_results": _followup_records(orch, store),
            "followup_invalid": [dict(x) for x in (orch.followup_invalid if orch else ())],
            "new_actions_error": orch.new_actions_error if orch else None,
            "followup_error": orch.followup_error if orch else None,
        }
        # 검증을 통과한 분석·검증 결과를 create-only로 저장한다 (원본 Audio·Transcript는 건드리지 않는다).
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with open(review_path, "x", encoding="utf-8") as f:
            json.dump({"reviews": result["reviews"], "rejected": result["rejected"]}, f, ensure_ascii=False, indent=2)
        if result["analysis"] is not None:
            transcript = json.loads(Path(result["transcript"]).read_text(encoding="utf-8"))
            save_analysis(result["analysis"], transcript, root / "meetings")
        with open(run_path, "x", encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False, indent=2)
        return _build_result(mid, root)
    finally:
        with _running_lock:
            _running.discard(mid)


# ---------------------------------------------------------------- Human Review (review 도메인을 호출만 한다)

_review_lock = threading.Lock()


def _review(fn) -> ActionView:
    """상태전이 규칙은 review 도메인과 ActionStore가 소유한다. 여기서는 예외를 HTTP 오류로 옮기기만 한다."""
    root = get_data_root()
    store = ActionStore(root / "actions" / "actions.json")
    with _review_lock:
        try:
            action_id = fn(store)
            action = store.get(action_id)
        except KeyError:
            raise ApiError(404, "action_not_found", "Action을 찾을 수 없습니다.") from None
        except CompletionGuardError:
            raise ApiError(409, "completion_guard_violation", "승인 조건을 만족하지 않아 완료 처리할 수 없습니다.") from None
        except TransitionError:
            raise ApiError(409, "invalid_transition", "현재 상태에서는 허용되지 않는 처리입니다.") from None
        except ValueError as e:  # 선택지·승인자·승인 근거 검증 (메시지는 도메인의 한국어 안내문)
            raise ApiError(422, "invalid_review_request", str(e)) from None
    return ActionView(**_action_view(action, root, {}))


def review(action_id: str, choice, actor_name, meeting_id=None, timestamp=None) -> ActionView:
    def run(store):
        review_action(store, action_id, choice, actor_name=actor_name, meeting_id=meeting_id, timestamp=timestamp)
        return action_id
    return _review(run)


def withdraw(action_id: str, actor_name) -> ActionView:
    def run(store):
        withdraw_completion(store, action_id, actor_name=actor_name)
        return action_id
    return _review(run)
