"""STT 계층 (Audio → Transcript). 이후 분석은 기존 pipeline이 맡는다."""
from pathlib import Path
from typing import List, Mapping, Optional

from meeting_action_agent.stt.base import SttError, SttProvider
from meeting_action_agent.stt.transcript import (
    TranscriptError,
    build_transcript,
    save_original_transcript,
)

__all__ = ["SttError", "SttProvider", "TranscriptError", "build_transcript", "run_stt", "save_original_transcript"]


def run_stt(provider: SttProvider, audio_path, meeting_id: str, speaker_map: Optional[Mapping] = None) -> List[dict]:
    """Provider로 전사한 원시 세그먼트를 Transcript 계약으로 변환한다 (저장하지 않음)."""
    return build_transcript(provider.transcribe(Path(audio_path)), meeting_id, speaker_map)
