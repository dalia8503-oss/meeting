"""Audio → STT → Transcript → Analyst → Reviewer → Action Manager 연결 (service 계층).

새 분석 로직은 없다. scripts/run_audio_pipeline.py(CLI)와 API가 같은 함수를 호출한다.
"""
import json
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from meeting_action_agent.ingest import ingest_audio, safe_meeting_id, verify_audio_hash
from meeting_action_agent.pipeline import extract_review_convert
from meeting_action_agent.store import ActionStore
from meeting_action_agent.stt import run_stt, save_original_transcript


def run_audio_pipeline(
    audio,
    *,
    meeting_id: str,
    meeting_date: str,
    root,
    store_path,
    provider,
    analyst_runner: Callable,
    reviewer_runner: Callable,
    speaker_map: Optional[Mapping] = None,
    participants: Optional[Sequence[str]] = None,
    title: Optional[str] = None,
    followup_runner: Optional[Callable] = None,
    followup_reviewer_runner: Optional[Callable] = None,
    previous_meeting_ids: Optional[Sequence[str]] = None,
) -> dict:
    root = Path(root)
    metadata = {"meeting_id": meeting_id, "meeting_date": meeting_date}
    if title:
        metadata["title"] = title
    if participants:
        metadata["participants"] = list(participants)
    if speaker_map:
        metadata["speaker_map"] = dict(speaker_map)

    hash_file = root / "meetings" / f"{safe_meeting_id(meeting_id)}.audio.json"
    if hash_file.exists():  # 이전 실행의 원본 재사용 (덮어쓰지 않음, 해시로 변조 확인)
        if not verify_audio_hash(root, meeting_id):
            raise RuntimeError("원본 녹음 해시가 기록과 다릅니다 (변경 의심) — 중단")
        record = json.loads(hash_file.read_text(encoding="utf-8"))
        audio_path = root / record["file"]
    else:
        audio_path = ingest_audio(audio, metadata, root).audio_path

    transcript_path = root / "transcripts" / "original" / f"{meeting_id}.json"
    if transcript_path.exists():  # 원본 녹취는 재생성·덮어쓰기 금지
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    else:
        transcript = run_stt(provider, audio_path, meeting_id, speaker_map)
        transcript_path = save_original_transcript(transcript, root)

    store = ActionStore(store_path)
    orchestration = None
    if followup_runner is None:  # 기존 호환 경로 (CLI): 단일 Agent 순차
        reviewed = extract_review_convert(transcript, analyst_runner, reviewer_runner, store=store, metadata=metadata)
    else:  # T-11 Multi-Agent: 1차/2차 판단은 Orchestrator가 한다
        from meeting_action_agent.orchestrator import run_orchestrated_meeting
        orchestration = run_orchestrated_meeting(
            transcript, analyst_runner=analyst_runner, candidate_reviewer_runner=reviewer_runner,
            followup_runner=followup_runner, followup_reviewer_runner=followup_reviewer_runner or reviewer_runner,
            store=store, metadata=metadata, previous_meeting_ids=previous_meeting_ids,
            meetings_dir=root / "meetings")
        reviewed = orchestration.new_actions
    if reviewed is None:  # 신규 경로가 격리된 도메인 오류로 실패 (orchestration.new_actions_error)
        return {
            "audio": str(audio_path), "transcript": str(transcript_path), "utterances": len(transcript),
            "actions": store.load_all(), "rejected": [], "reviews": [], "analysis": None, "outcomes": [],
            "orchestration": orchestration,
        }
    return {
        "orchestration": orchestration,
        "audio": str(audio_path), "transcript": str(transcript_path), "utterances": len(transcript),
        "actions": store.load_all(),
        "rejected": list(reviewed.rejected), "reviews": list(reviewed.reviews),
        "analysis": reviewed.analysis, "outcomes": list(reviewed.conversion.outcomes),
    }


def _gemini_json_runner(prompt: str) -> str:
    """Reviewer용: JSON 모드만 사용 (scripts/smoke_gemini.py와 동일 방식)."""
    import os

    from google import genai
    from google.genai import types

    from meeting_action_agent.llm import GEMINI_DEFAULT_MODEL

    client = genai.Client(http_options=types.HttpOptions(timeout=int(float(os.environ.get("GEMINI_TIMEOUT_SEC", 60)) * 1000)))
    r = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return r.text
