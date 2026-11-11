"""수동 smoke (자동 pytest 아님): 실제 Claude → analyze_transcript → pipeline → Action.

실행: python scripts/smoke_claude.py   (ANTHROPIC_API_KEY 필요)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meeting_action_agent.analyst import analyze_transcript
from meeting_action_agent.llm import claude_runner
from meeting_action_agent.pipeline import extract_and_convert
from meeting_action_agent.store import ActionStore

MID = "20261110-01"
TRANSCRIPT = [
    {"meeting_id": MID, "speaker": "팀장", "timestamp": "00:01:00", "text": "김대리, 금요일까지 도면 수정해서 공유해주세요."},
    {"meeting_id": MID, "speaker": "김대리", "timestamp": "00:01:10", "text": "네, 알겠습니다."},
    {"meeting_id": MID, "speaker": "박대리", "timestamp": "00:01:30", "text": "다음 회의에서는 품질 문제도 다뤄보면 어떨까요?"},
    {"meeting_id": MID, "speaker": "이대리", "timestamp": "00:01:45", "text": "도면 검토는 끝났나요?"},
]
META = {"meeting_id": MID, "meeting_date": "2026-11-10", "participants": ["팀장", "김대리", "박대리", "이대리"]}

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY 환경변수가 필요합니다.")

raw = {}
def runner(prompt):
    raw["text"] = claude_runner(prompt)
    return raw["text"]

try:
    analysis = analyze_transcript(TRANSCRIPT, runner)  # JSON 파싱 + validate_analysis
except Exception as e:
    print("[분석 단계 실패]", type(e).__name__, e)
    print("--- raw 응답 ---\n", raw.get("text"))
    sys.exit(1)
print("== action_candidates ==")
print(json.dumps(analysis["action_candidates"], ensure_ascii=False, indent=1))
print("== open_issues ==")
print(json.dumps(analysis["open_issues"], ensure_ascii=False, indent=1))
store = ActionStore(Path(tempfile.mkdtemp()) / "actions.json")
result = extract_and_convert(TRANSCRIPT, lambda p: analysis, store=store, metadata=META)
print("== outcomes ==", [(o.kind, o.action_id, o.assignee) for o in result.outcomes])
print("== actions ==")
print(json.dumps(store.load_all(), ensure_ascii=False, indent=1))
