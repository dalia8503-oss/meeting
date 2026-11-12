"""DEMO 모드 provider/runner. 외부 API를 호출하지 않는다.

고정 STT 응답(demo_data/stt_response.json, 손으로 작성한 fixture)과 그 녹취에 맞춘 고정 분석/검증 응답을 낸다.
이후 Transcript 검증 → Analyst 검증 → Reviewer 코드 검사 → Action Manager는 LIVE와 같은 기존 코드가 실행한다.
업로드한 파일의 실제 음성 내용은 분석하지 않는다 (DEMO는 고정 시나리오다).
"""
import json
from pathlib import Path

from ..stt.gemini_stt import GeminiSttProvider

_DATA = Path(__file__).resolve().parent / "demo_data"
DEMO_SPEAKER_MAP = {
    "speaker_1": {"name": "팀장"}, "speaker_2": {"name": "김대리"},
    "speaker_3": {"name": "박대리"}, "speaker_4": {"name": "이대리"},
}
_SENT = "김대리, 금요일까지 도면 수정해서 공유해주세요."


def demo_stt_provider() -> GeminiSttProvider:
    raw = (_DATA / "stt_response.json").read_text(encoding="utf-8")
    return GeminiSttProvider(call=lambda *a: raw)


def make_demo_analyst(meeting_id: str):
    def runner(prompt: str) -> str:
        return json.dumps({
            "meeting_id": meeting_id,
            "key_discussions": [{"text": "지난주 진행 상황과 도면 작업 현황을 확인했다.", "evidence_timestamps": ["00:00:08"]}],
            "decisions": [],
            "open_issues": [
                {"text": "다음 회의에서 품질 문제를 다룰지 제안됨 (결정되지 않음).", "evidence_timestamps": ["00:00:20"]},
            ],
            "needs_confirmation": [
                {"text": "도면 검토가 끝났는지 질문이 나왔으나 답변이 없다.", "evidence_timestamps": ["00:00:25"]},
            ],
            "action_candidates": [{"instruction_type": "지시·요청", "candidate": {
                "meeting_id": meeting_id, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정해서 공유",
                "due_date": "금요일까지", "due_date_normalized": None, "evidence": _SENT,
                "timestamp": "00:00:12", "status": "신규", "flags": []}}],
        }, ensure_ascii=False)
    return runner


def demo_reviewer(prompt: str) -> dict:
    return {"target": {"type": "action", "ref": "00:00:12"}, "verdict": "PASS", "reasons": [], "attempt": 0}
