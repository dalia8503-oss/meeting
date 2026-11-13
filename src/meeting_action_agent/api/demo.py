"""DEMO 모드 provider/runner. 외부 API를 호출하지 않는다.

고정 STT 응답(demo_data/stt_response.json, 손으로 작성한 fixture)과 그 녹취에 맞춘 고정 분석/검증 응답을 낸다.
이후 Transcript 검증 → Analyst 검증 → Reviewer 코드 검사 → Action Manager는 LIVE와 같은 기존 코드가 실행한다.
업로드한 파일의 실제 음성 내용은 분석하지 않는다 (DEMO는 고정 시나리오다).
"""
import json
import re
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


def _input_after(prompt: str, marker: str):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def demo_followup_runner(prompt: str) -> str:
    """DEMO 이행점검: 고정 시나리오 녹취는 이전 Action을 언급하지 않는다 → 확인 필요 + 근거 없음 (Action 불변)."""
    payload = _input_after(prompt, "## INPUT\n")
    return json.dumps({
        "meeting_id": payload["transcript"][0]["meeting_id"], "action_id": payload["action"]["action_id"],
        "proposed_status": "확인 필요", "confidence": "하", "evidence": [], "flags_to_add": [],
        "reason": "DEMO 고정 녹취에는 이전 Action 언급이 없다.",
    }, ensure_ascii=False)


def demo_followup_reviewer(prompt: str) -> dict:
    ref = _input_after(prompt, "## INPUT\n")["followup_result"]["action_id"]
    return {"target": {"type": "followup", "ref": ref}, "verdict": "PASS", "reasons": [], "attempt": 0}


_VOCATIVE = re.compile(r"(?:(\d+\s?부)\s?)?(?:(\d+\s?과)\s?)?([가-힣]\s?[가-힣]{0,2}\s?(?:부장|차장|과장|대리|사원|주임|팀장|반장|소장))\s?(?:님)?\s?[,،]")


def demo_speaker_runner(prompt: str) -> str:
    """DEMO 화자 추천: 직접 호명 직후 다른 화자가 응답하면 그 화자를 호명된 이름으로 추천한다 (외부 호출 없음).
    그 외에는 근거 없음(null). 추천은 미확정이며 사람이 확인해야 한다."""
    transcript = _input_after(prompt, "## INPUT\n")["transcript"]
    out: dict = {u["speaker"]: {"suggested_name": None, "confidence": 0.0, "evidence": [], "needs_confirmation": True}
                 for u in transcript}
    for prev, nxt in zip(transcript, transcript[1:]):
        m = _VOCATIVE.search(prev["text"])
        if m and nxt["speaker"] != prev["speaker"] and out[nxt["speaker"]]["suggested_name"] is None:
            name = re.sub(r"\s+", "", m.group(3))
            dept = re.sub(r"\s+", "", m.group(1)) if m.group(1) else None
            sect = re.sub(r"\s+", "", m.group(2)) if m.group(2) else None
            out[nxt["speaker"]] = {
                "suggested_name": name, "department": dept, "section": sect, "confidence": 0.9, "needs_confirmation": False,
                "evidence": [f"{prev['speaker']}가 '{name}'을(를) 직접 호명한 직후({nxt['timestamp']}) 이 화자가 응답함"]}
    return json.dumps(out, ensure_ascii=False)
