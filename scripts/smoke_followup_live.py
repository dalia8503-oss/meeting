"""수동 LIVE smoke (자동 pytest 아님, T-09 Previous Action Follow-up E2E). 시나리오 1회만 실행한다.

!! 실행하면 실제 Gemini API를 호출한다(약 4회, 비용 발생). 반드시 --live 를 명시해야 실행된다.
   pytest는 이 파일을 수집/실행하지 않는다(tests/ 밖, test_ 접두어 없음).
   API key는 환경변수 GEMINI_API_KEY(또는 .env)로만 받는다.

- synthetic Transcript만 사용 (회사 실제 녹음/내용 금지). 새 temp runtime을 쓴다.
- run_meeting_pipeline()에 실제 Gemini runner를 주입한다. production 로직을 복사하지 않는다.
- 결과 파일에는 API key/credential을 저장하지 않는다. 재실행은 하지 않는다.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_ap = argparse.ArgumentParser(description="실제 Gemini API를 호출하는 수동 LIVE smoke (약 4회 호출). 자동 테스트 아님.")
_ap.add_argument("--live", action="store_true", help="실제 Gemini 호출에 동의한다는 명시 플래그")
if not _ap.parse_args().live:
    _ap.error("실제 Gemini를 호출한다. 진행하려면 --live 를 지정하라")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meeting_action_agent.env import load_env
from meeting_action_agent.llm import GEMINI_DEFAULT_MODEL, gemini_runner
from meeting_action_agent.manager import convert_candidate
from meeting_action_agent.pipeline import run_meeting_pipeline
from meeting_action_agent.store import ActionStore

load_env()
if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY 필요")
MODEL = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)

RUNTIME = Path(tempfile.mkdtemp(prefix="maa_followup_live_smoke_"))  # 실행마다 새 임시 runtime

PREV, CUR = "20261110-01", "20261117-01"
PREV_TRANSCRIPT_LINE = {"meeting_id": PREV, "speaker": "팀장", "timestamp": "00:01:00",
                        "text": "김대리, 금요일까지 도면 수정해서 공유해주세요."}
CUR_TRANSCRIPT = [
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": "00:01:00", "text": "김 대리, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?"},
    {"meeting_id": CUR, "speaker": "김대리", "timestamp": "00:01:10", "text": "수정 완료해서 어제 공유했습니다."},
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": "00:01:30", "text": "좋습니다. 그러면 이번에는 박 대리님이 다음주 화요일까지 검토 의견 정리해서 공유해 주세요."},
    {"meeting_id": CUR, "speaker": "박대리", "timestamp": "00:01:40", "text": "네, 알겠습니다."},
    {"meeting_id": CUR, "speaker": "팀장", "timestamp": "00:02:00", "text": "다음에는 품질 문제도 한번 보면 좋겠습니다."},
]
META = {"meeting_id": CUR, "meeting_date": "2026-11-17", "participants": ["팀장", "김대리", "박대리"]}
PREV_META = {"meeting_id": PREV, "meeting_date": "2026-11-10", "participants": ["팀장", "김대리"]}

store = ActionStore(RUNTIME / "actions" / "actions.json")
# A-001: 기존 Action 생성 경로(convert_candidate)로 만든다. (Gemini 호출 없음)
convert_candidate(
    {"meeting_id": PREV, "assigner": "팀장", "assignee": "김대리", "task": "도면 수정해서 공유",
     "due_date": "금요일까지", "due_date_normalized": None, "evidence": PREV_TRANSCRIPT_LINE["text"],
     "timestamp": PREV_TRANSCRIPT_LINE["timestamp"], "status": "신규", "flags": []},
    utterance=PREV_TRANSCRIPT_LINE, mentions=None, metadata=PREV_META, reviewer_passed=True, store=store)
a1_before = store.get("A-001")

calls = []


def _json_runner(kind):
    from google import genai
    from google.genai import types

    def run(prompt):
        client = genai.Client(http_options=types.HttpOptions(timeout=90000))
        t = time.time()
        r = client.models.generate_content(
            model=MODEL, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
        calls.append({"kind": kind, "sec": round(time.time() - t, 2), "raw": r.text})
        return r.text
    return run


def analyst_runner(prompt):
    t = time.time()
    out = gemini_runner(prompt)
    calls.append({"kind": "analyst", "sec": round(time.time() - t, 2), "raw": out})
    return out


result = {"model": MODEL, "runtime": str(RUNTIME), "status": None, "transcript_utterances": len(CUR_TRANSCRIPT),
          "transcript_source": "synthetic (STT 미사용)"}
t0 = time.time()
try:
    r = run_meeting_pipeline(
        CUR_TRANSCRIPT, analyst_runner=analyst_runner, candidate_reviewer_runner=_json_runner("candidate_reviewer"),
        followup_runner=_json_runner("followup"), followup_reviewer_runner=_json_runner("followup_reviewer"),
        store=store, metadata=META, previous_meeting_ids=[PREV], meetings_dir=RUNTIME / "meetings")
    result["status"] = "ok"
    result["followup_outcomes"] = [
        {"action_id": o.action_id, "outcome": o.outcome, "from": o.from_status, "to": o.to_status,
         "detail": o.detail, "review": o.review, "followup_calls": o.followup_calls, "review_calls": o.review_calls}
        for o in r.followup_outcomes]
    result["followup_invalid"] = list(r.followup_invalid)
    result["new_actions_error"] = r.new_actions_error
    if r.new_actions:
        result["new_action_reviews"] = list(r.new_actions.reviews)
        result["new_action_rejected"] = list(r.new_actions.rejected)
        result["analysis"] = r.new_actions.analysis
    result["snapshot_path"] = str(r.snapshot_path) if r.snapshot_path else None
except Exception as e:
    result["status"] = f"error: {type(e).__name__}: {e}"
result["elapsed_sec"] = round(time.time() - t0, 2)
result["gemini_calls"] = {"total": len(calls), **{k: sum(1 for c in calls if c["kind"] == k)
                                                  for k in {c["kind"] for c in calls}}}
result["call_log"] = calls
result["a001_before"] = a1_before
result["actions_final"] = store.load_all()
snap = RUNTIME / "meetings" / f"{CUR}.followups.json"
result["followup_snapshot"] = json.loads(snap.read_text(encoding="utf-8")) if snap.exists() else None
out = Path(__file__).resolve().parents[1] / "smoke_results" / "gemini_followup_live_result.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("status:", result["status"], "| calls:", result["gemini_calls"], "| elapsed:", result["elapsed_sec"])
print("saved:", out)
