"""`/analyze-meeting` 진입점 (T-05-5) — 얇은 CLI. 규칙은 재구현하지 않는다.

Transcript + MeetingAnalysis(JSON, Meeting Analyst 출력) + metadata
  → validate_analysis → save_analysis → pipeline.extract_and_convert → Action Manager.
실제 LLM 호출은 하지 않는다 (분석 JSON은 Analyst 에이전트가 만들어 파일로 넘긴다).

사용: python -m meeting_action_agent.analyze_meeting TRANSCRIPT.json ANALYSIS.json METADATA.json STORE.json [MEETINGS_DIR]
"""
import json
import sys
from pathlib import Path

from meeting_action_agent.analyst import save_analysis
from meeting_action_agent.pipeline import extract_and_convert
from meeting_action_agent.store import ActionStore


def analyze_meeting(transcript, analysis, metadata, store, meetings_dir=None) -> dict:
    """검증 실패 시 예외(저장·변환 없음). 성공 시 요약 → 결정 → 지시 → 미결 → 확인 필요 순 보고용 dict."""
    if meetings_dir:
        save_analysis(analysis, transcript, meetings_dir)  # 내부에서 validate_analysis
    result = extract_and_convert(transcript, lambda _prompt: analysis, store=store, metadata=metadata)
    return {
        "summary": analysis["key_discussions"], "decisions": analysis["decisions"],
        "actions": [(o.kind, o.action_id, o.assignee) for o in result.outcomes],
        "open_issues": analysis["open_issues"], "needs_confirmation": analysis["needs_confirmation"],
    }


def main(argv) -> int:
    if len(argv) not in (5, 6):
        print(__doc__)
        return 2
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    report = analyze_meeting(load(argv[1]), load(argv[2]), load(argv[3]), ActionStore(argv[4]),
                             argv[5] if len(argv) == 6 else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
