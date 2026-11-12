"""STT Provider 경계. 책임은 Audio → 원시 세그먼트까지다 (요약·Action 추출·분석 금지).

원시 세그먼트: {"speaker": str, "timestamp": str, "text": str}
(Transcript 계약 변환·검증은 transcript.py가 한다. Provider는 Gemini 외 Provider로 교체 가능해야 한다.)
"""
from pathlib import Path
from typing import List, Protocol, runtime_checkable

SUPPORTED_AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


class SttError(RuntimeError):
    """STT 호출·응답 형식 오류."""


@runtime_checkable
class SttProvider(Protocol):
    def transcribe(self, audio_path: Path) -> List[dict]: ...
