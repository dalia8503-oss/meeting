"""수동 LIVE smoke (자동 pytest 아님, T-BRIEF-API+TTS). 1회만 실행한다.

!! 실제 Gemini API를 호출한다(정상이면 Briefing Agent 1 + Reviewer 1 = 2회). 반드시 --live 를 명시해야 한다.
   실제 로컬 Windows TTS(ko-KR)로 WAV 파일을 생성한다(스피커 재생은 하지 않는다). 외부 TTS 없음.
- synthetic 데이터만 사용(회사 실제 데이터 금지). 새 temp runtime을 쓴다. API key는 출력/저장하지 않는다.
- API는 FastAPI TestClient로 호출한다. 호출 수는 runner를 감싸 계측한다.
"""
import argparse
import copy
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--live", action="store_true", help="실제 Gemini 호출에 동의")
if not _ap.parse_args().live:
    _ap.error("실제 Gemini를 호출한다. 진행하려면 --live 를 지정하라")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meeting_action_agent.env import load_env

load_env()
if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY 필요")

RUNTIME = Path(tempfile.mkdtemp(prefix="maa_briefing_smoke_"))
os.environ["MAA_RUNTIME_DIR"] = str(RUNTIME)
os.environ["MAA_MODE"] = "demo"

from fastapi.testclient import TestClient

from meeting_action_agent.api import briefing_service
from meeting_action_agent.api.app import create_app
from meeting_action_agent.audio_pipeline import _gemini_json_runner
from meeting_action_agent.store import ActionStore

MID, TODAY = "20261110-01", "2026-11-12"
data = RUNTIME / "demo"
(data / "meetings").mkdir(parents=True)
(data / "meetings" / f"{MID}.meta.json").write_text(
    json.dumps({"meeting_id": MID, "meeting_date": "2026-11-10", "title": "도면 검토 회의(가상)"}, ensure_ascii=False), encoding="utf-8")
(data / "meetings" / f"{MID}.analysis.json").write_text(json.dumps({
    "meeting_id": MID,
    "key_discussions": [{"text": "도면 수정 일정과 품질 검토 방향을 논의했다", "evidence_timestamps": ["00:01:00"]}],
    "decisions": [],
    "open_issues": [{"text": "품질 문제를 추가로 검토할지 정해지지 않았다", "evidence_timestamps": ["00:03:00"]}],
    "needs_confirmation": [{"text": "검토 의견 정리의 기한이 불명확하다", "evidence_timestamps": ["00:04:00"]}],
    "action_candidates": []}, ensure_ascii=False), encoding="utf-8")

example = json.loads((ROOT / "specs/001-meeting-action-agent/contracts/examples/valid/action.json").read_text(encoding="utf-8"))
store = ActionStore(data / "actions" / "actions.json")
for aid, who, task, due, norm in [("A-001", "김대리", "도면 수정해서 공유", "금요일까지", "2026-11-13"),
                                  ("A-002", "박대리", "검토 의견 정리", "다음주 화요일까지", "2026-11-17")]:
    a = copy.deepcopy(example)
    a.update(action_id=aid, meeting_id=MID, assignee=who, task=task, due_date=due, due_date_normalized=norm, status="신규")
    a["history"][0]["to_status"] = "신규"
    store.create(a)
store.change_status("A-001", "완료 후보", "Agent")

calls = {"generator": 0, "reviewer": 0}


def gen(prompt):
    calls["generator"] += 1
    return _gemini_json_runner(prompt)


def rev(prompt):
    calls["reviewer"] += 1
    return _gemini_json_runner(prompt)


briefing_service._runners = lambda: (gen, rev)  # 실제 Gemini, 호출 수만 계측. TTS는 실제 로컬 Windows provider.

client = TestClient(create_app())
r = client.post("/api/briefings", json={"previous_meeting_id": MID, "current_date": TODAY})
print("HTTP", r.status_code)
body = r.json()
print("calls:", calls)
if r.status_code != 200:
    print(json.dumps(body, ensure_ascii=False))
    sys.exit(1)
print(json.dumps(body, ensure_ascii=False, indent=1))

audio = client.get(body["audio"]["url"])
wav_bytes = audio.content
out = ROOT / "smoke_results" / "briefing_smoke.wav"
out.parent.mkdir(exist_ok=True)
out.write_bytes(wav_bytes)
with wave.open(str(out)) as w:
    print("WAV:", audio.status_code, audio.headers["content-type"], "bytes", len(wav_bytes),
          "header", wav_bytes[:4], "duration_sec", round(w.getnframes() / w.getframerate(), 2),
          "rate", w.getframerate(), "ch", w.getnchannels())
again = client.post("/api/briefings", json={"previous_meeting_id": MID, "current_date": TODAY}).json()
print("replay reused:", again["reused"], "calls after replay:", calls)
saved = json.loads((data / "briefings" / f"{body['briefing_id']}.json").read_text(encoding="utf-8"))
print("saved text == response text:", saved["briefing_text"] == body["briefing_text"])
print("wav saved at", out)
