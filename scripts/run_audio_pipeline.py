"""MVP 실행: 녹음 파일 → STT → Transcript → Meeting Analyst → Reviewer → Action Manager → Action List.

새 분석 로직은 없다. 이 스크립트의 새 책임은 Audio → Transcript까지이고, 이후는 기존
`pipeline.extract_review_convert`를 그대로 호출한다 (STT 호출과 Analyst 호출은 별개 Provider 경계).

실행 (실제 Gemini API 호출 — GEMINI_API_KEY 필요):
  python scripts/run_audio_pipeline.py AUDIO --meeting-id 20261110-01 --meeting-date 2026-11-10 \
      [--speaker-map speaker_map.json] [--participants 김대리 팀장] [--root DATA_DIR] [--store actions/actions.json]

speaker_map.json 예: {"speaker_1": {"name": "팀장"}, "speaker_2": {"name": "김대리"}}  (사람이 확인한 매핑만)
없으면 speaker_N 라벨을 그대로 쓴다.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meeting_action_agent.audio_pipeline import _gemini_json_runner, run_audio_pipeline  # noqa: F401  (service 계층 재사용)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--meeting-id", required=True)
    ap.add_argument("--meeting-date", required=True, help="YYYY-MM-DD (기한 환산 기준)")
    ap.add_argument("--speaker-map", help="사람이 확인한 화자 매핑 JSON 파일")
    ap.add_argument("--participants", nargs="*")
    ap.add_argument("--title")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="audio/·transcripts/·meetings/가 생길 기준 폴더")
    ap.add_argument("--store", default=None, help="Action List JSON (기본: <root>/actions/actions.json)")
    a = ap.parse_args(argv)

    from meeting_action_agent.env import load_env
    from meeting_action_agent.llm import gemini_runner
    from meeting_action_agent.stt.gemini_stt import GeminiSttProvider

    load_env()
    smap = json.loads(Path(a.speaker_map).read_text(encoding="utf-8")) if a.speaker_map else None
    result = run_audio_pipeline(
        a.audio, meeting_id=a.meeting_id, meeting_date=a.meeting_date, root=a.root,
        store_path=a.store or Path(a.root) / "actions" / "actions.json",
        provider=GeminiSttProvider(), analyst_runner=gemini_runner, reviewer_runner=_gemini_json_runner,
        speaker_map=smap, participants=a.participants, title=a.title,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
