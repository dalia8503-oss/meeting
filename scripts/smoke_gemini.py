"""수동 smoke (자동 pytest 아님): 실제 Gemini → analyze_transcript → pipeline → Action.

실행: python scripts/smoke_gemini.py   (GEMINI_API_KEY 필요)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meeting_action_agent.env import load_env
from meeting_action_agent.analyst import analyze_transcript
from meeting_action_agent.llm import gemini_runner
from meeting_action_agent.pipeline import extract_review_convert
from meeting_action_agent.store import ActionStore

MID = "20261110-01"
TRANSCRIPT = [
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:01:00", "text": "김대리, 금요일까지 도면 수정해서 공유해주세요."},
    {"meeting_id": MID, "speaker": "김대리", "timestamp": "00:01:10", "text": "네, 알겠습니다."},
    {"meeting_id": MID, "speaker": "박대리", "timestamp": "00:01:30", "text": "다음 회의에서는 품질 문제도 다뤄보면 어떨까요?"},
    {"meeting_id": MID, "speaker": "이대리", "timestamp": "00:01:45", "text": "도면 검토는 끝났나요?"},
]
META = {"meeting_id": MID, "meeting_date": "2026-11-10", "participants": ["팀장", "김대리", "박대리", "이대리"]}

load_env()
if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY 환경변수가 필요합니다.")
MODEL = os.environ.get("GEMINI_MODEL", "")

raw = {"analyst": None, "reviewer": []}
analysis_box = {}


def analyst_runner(prompt):
    raw["analyst"] = gemini_runner(prompt)
    return raw["analyst"]


def reviewer_runner(prompt):
    """Reviewer용: 스키마는 prompt에 포함돼 있으므로 JSON 모드만 사용(분석 스키마 강제 회피)."""
    from google import genai
    from google.genai import types
    timeout = float(os.environ.get("GEMINI_TIMEOUT_SEC", 60))
    client = genai.Client(http_options=types.HttpOptions(timeout=int(timeout * 1000)))
    r = client.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    raw["reviewer"].append(r.text)
    return r.text


out_dir = Path(__file__).resolve().parents[1] / "smoke_results"
out_dir.mkdir(exist_ok=True)
result = {"provider": "gemini", "model": MODEL, "status": None}
store = ActionStore(Path(tempfile.mkdtemp()) / "actions.json")
t0 = time.time()
try:
    reviewed = extract_review_convert(TRANSCRIPT, analyst_runner, reviewer_runner, store=store, metadata=META)
    result["status"] = "ok"
    try:
        result["analysis"] = json.loads(raw["analyst"])
    except Exception:
        result["analysis"] = raw["analyst"]
    result["review"] = {"reviews": list(reviewed.reviews), "rejected": list(reviewed.rejected)}
    result["actions"] = store.load_all()
except Exception as e:
    result["status"] = f"error: {type(e).__name__}: {e}"
    result["analyst_raw"] = raw["analyst"]
    result["reviewer_raw"] = raw["reviewer"]
    result["review"] = None
    result["actions"] = store.load_all()
result["elapsed_sec"] = round(time.time() - t0, 2)
path = out_dir / "gemini_smoke_result.json"
path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=1))
print("saved:", path)
