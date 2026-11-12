"""T-02-1: Gemini Audio STT Provider (MVP). Audio → 원시 세그먼트까지만 책임진다.

Meeting Analyst 호출(llm.gemini_runner)과 섞지 않는다. 환경변수: GEMINI_API_KEY(.env 또는 환경),
GEMINI_STT_MODEL(없으면 GEMINI_MODEL, 그것도 없으면 llm.GEMINI_DEFAULT_MODEL), GEMINI_STT_TIMEOUT_SEC.
키는 코드·로그·파일에 남기지 않는다. google-genai는 호출 시점에 import한다 (pytest는 외부 API를 부르지 않는다).

한계: 오디오를 inline으로 보내므로 크기 상한이 있다(MAX_INLINE_BYTES). 큰 파일은 Files API 필요(후속).
Gemini는 timestamp·화자 라벨을 정확하다고 보장하지 않는다. 신뢰도 지표도 없다.
"""
import json
import os
import time
from pathlib import Path
from typing import Callable, List, Optional

from meeting_action_agent.stt.base import SUPPORTED_AUDIO_MIME, SttError

MAX_INLINE_BYTES = 18 * 1024 * 1024  # inline 요청 한도(약 20MB)에 여유를 둔다
DEFAULT_TIMEOUT_SEC = 180

STT_PROMPT = """첨부된 회의 녹음을 한국어로 받아 적어라. 너의 역할은 전사(받아쓰기)뿐이다.

규칙:
- 들리는 말을 원문 그대로 적는다. 요약하지 않는다.
- 문장을 추가하지 않는다. 말하지 않은 내용을 만들지 않는다.
- 의미를 보정하거나 문장을 매끄럽게 고치지 않는다. 말투·군더더기도 들리는 대로 적는다.
- 들리지 않거나 알아들을 수 없는 구간은 지어내지 말고 그 구간은 건너뛴다.
- 지시사항 추출, 분석, 해석, 판단은 하지 않는다.
- 화자는 목소리로 구분해 speaker_1, speaker_2, speaker_3 … 형태의 라벨만 쓴다. 처음 등장한 화자가 speaker_1이다.
- 실제 사람 이름이나 직책을 화자로 추측하지 않는다. 발언 안에서 이름이 불려도 화자 라벨은 speaker_N만 쓴다.
- 화자가 바뀌거나 새 발언이 시작될 때마다 세그먼트를 나눈다.
- timestamp는 각 발언이 시작되는 녹음 시작 기준 경과시간으로 MM:SS 형식(1시간 이상이면 HH:MM:SS)으로 쓴다.

JSON으로만 답한다: {"segments": [{"speaker": "speaker_1", "timestamp": "00:00", "text": "..."}]}
"""

SEGMENTS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "timestamp", "text"],
            },
        }
    },
    "required": ["segments"],
}


def parse_response(text: str) -> List[dict]:
    """응답 JSON → [{speaker, timestamp, text}]. 알 수 없는 필드는 버리고 text는 그대로 둔다."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise SttError(f"STT 응답이 JSON이 아닙니다: {e}") from None
    items = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise SttError("STT 응답에 segments가 없습니다")
    out = []
    for i, s in enumerate(items):
        if not isinstance(s, dict) or not all(isinstance(s.get(k), str) for k in ("speaker", "timestamp", "text")):
            raise SttError(f"STT 응답 segments[{i}] 형식 오류 (speaker·timestamp·text 문자열 필요)")
        out.append({"speaker": s["speaker"], "timestamp": s["timestamp"], "text": s["text"]})
    return out


def _gemini_call(audio: bytes, mime: str, prompt: str) -> str:
    from google import genai
    from google.genai import errors, types

    from meeting_action_agent.env import load_env
    from meeting_action_agent.llm import GEMINI_DEFAULT_MODEL

    load_env()
    model = os.environ.get("GEMINI_STT_MODEL") or os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    timeout = float(os.environ.get("GEMINI_STT_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
    client = genai.Client(http_options=types.HttpOptions(timeout=int(timeout * 1000)))  # GEMINI_API_KEY 사용
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_json_schema=SEGMENTS_RESPONSE_SCHEMA,
    )
    for attempt in range(3):  # 일시적 과부하(503)만 재시도
        try:
            response = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=audio, mime_type=mime), prompt],
                config=config,
            )
            break
        except errors.ServerError as e:
            if e.code != 503 or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    if response.text is None:
        raise RuntimeError(f"Gemini 응답 text 없음: {getattr(response, 'prompt_feedback', None)}")
    return response.text


class GeminiSttProvider:
    def __init__(self, call: Optional[Callable[[bytes, str, str], str]] = None):
        self._call = call or _gemini_call

    def transcribe(self, audio_path: Path) -> List[dict]:
        p = Path(audio_path)
        mime = SUPPORTED_AUDIO_MIME.get(p.suffix.lower())
        if mime is None:
            raise SttError(f"지원하지 않는 오디오 형식: {p.suffix}")
        size = p.stat().st_size
        if size > MAX_INLINE_BYTES:
            raise SttError(f"파일 크기가 inline 전송 한도를 넘습니다: {size} > {MAX_INLINE_BYTES} bytes (Files API는 후속)")
        try:
            text = self._call(p.read_bytes(), mime, STT_PROMPT)
        except SttError:
            raise
        except Exception as e:
            raise SttError(f"Gemini STT 호출 실패: {type(e).__name__}: {e}") from None
        return parse_response(text)
