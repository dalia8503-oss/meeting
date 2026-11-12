"""Meeting Analyst 실행 계층 (T-04-2/3/4) — 얇은 연결 계층. 새 규칙 엔진을 만들지 않는다.

흐름: Transcript → prompt(에이전트 정의 + 계약 스키마) → runner(LLM) → MeetingAnalysis
      → validate_analysis(스키마·근거 실재·meeting_id·유일성) → save_analysis(검증 통과분만 저장).

- runner는 주입되는 callable(prompt:str) -> str(JSON) | dict. 모델 호출 방식은 이 계층이 정하지 않는다.
- speaker/text는 추측·보완하지 않는다. 재연결 key는 (meeting_id, timestamp)이며 원본 Utterance에서 가져온다.
- due_date_normalized는 Analyst가 계산하지 않는다 (T-03-9 코드 담당).
"""
import json
import os
from collections import Counter
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.contracts import CONTRACTS_DIR, receive_agent_output, validate
from meeting_action_agent.speakers import speaker_context_text

AGENT_DEFINITION = Path(__file__).resolve().parents[3] / ".claude" / "agents" / "meeting-analyst.md"


class AnalysisValidationError(ValueError):
    """MeetingAnalysis가 transcript 근거 검증을 통과하지 못함. 저장하지 않는다."""


def build_prompt(transcript: Sequence[Mapping], speaker_map: Optional[Mapping] = None) -> str:
    validate("transcript", list(transcript))
    agent = AGENT_DEFINITION.read_text(encoding="utf-8")
    schema = (CONTRACTS_DIR / "meeting-analysis.schema.json").read_text(encoding="utf-8")
    return (
        f"{agent}\n\n## 출력 스키마 (meeting-analysis.schema.json)\n{schema}\n\n"
        "## Transcript\n" + json.dumps(list(transcript), ensure_ascii=False, indent=1)
        + speaker_context_text(speaker_map)
        + "\n\n위 Transcript만 근거로 MeetingAnalysis JSON 하나만 출력하라."
    )


def validate_analysis(analysis, transcript: Sequence[Mapping]) -> None:
    """스키마·meeting_id·근거 timestamp 실재/유일성 검증. 실패 시 예외 (보완·추측 없음)."""
    receive_agent_output("meeting-analysis", analysis)  # 완료 가드 → 스키마
    validate("transcript", list(transcript))
    ids = {u["meeting_id"] for u in transcript}
    if len(ids) != 1:
        raise AnalysisValidationError(f"transcript meeting_id가 하나가 아님: {sorted(ids)}")
    meeting_id = next(iter(ids))
    if analysis["meeting_id"] != meeting_id:
        raise AnalysisValidationError(f"meeting_id 불일치: {analysis['meeting_id']} != {meeting_id}")

    counts = Counter(u["timestamp"] for u in transcript)

    def check(ts: str, where: str, allow_duplicate: bool = False) -> None:
        if counts[ts] == 0:
            raise AnalysisValidationError(f"{where}: timestamp {ts}가 transcript에 없음")
        if counts[ts] > 1 and not allow_duplicate:
            raise AnalysisValidationError(f"{where}: timestamp {ts} 발언이 {counts[ts]}건이라 원본을 특정할 수 없음")

    for section in ("key_discussions", "decisions", "open_issues", "needs_confirmation"):
        for i, item in enumerate(analysis[section]):
            for ts in item["evidence_timestamps"]:
                check(ts, f"{section}[{i}]")
    for i, item in enumerate(analysis["action_candidates"]):
        cand = item["candidate"]
        where = f"action_candidates[{i}]"
        if cand["meeting_id"] != meeting_id:
            raise AnalysisValidationError(f"{where}: candidate.meeting_id 불일치")
        # T-05-1/9: 유형과 flag 일치 — 조건부·지시여부애매는 같은 이름 flag가 있어야 한다.
        # (flag가 있으면 status '확인 필요'·조건 기재는 action-candidate 스키마가 강제)
        itype = item["instruction_type"]
        if itype in ("조건부", "지시여부애매") and itype not in cand["flags"]:
            raise AnalysisValidationError(f"{where}: instruction_type '{itype}'인데 flags에 '{itype}'가 없음")
        # 에이전트 정의: 근거 발언을 특정할 수 없으면 '지시여부애매' + '확인 필요'로 내야 한다 (안전 처리)
        safe_ambiguous = item["instruction_type"] == "지시여부애매" and cand["status"] == "확인 필요"
        check(cand["timestamp"], where, allow_duplicate=safe_ambiguous)


def relink_utterance(candidate: Mapping, transcript: Sequence[Mapping]) -> Mapping:
    """(meeting_id, timestamp)로 원본 Utterance를 찾는다. 정확히 1건만 허용 (0건·2건 이상은 오류)."""
    found = [
        u for u in transcript
        if u["meeting_id"] == candidate["meeting_id"] and u["timestamp"] == candidate["timestamp"]
    ]
    if not found:
        raise AnalysisValidationError(f"timestamp {candidate['timestamp']}가 transcript에 없음")
    if len(found) > 1:
        raise AnalysisValidationError(
            f"timestamp {candidate['timestamp']} 발언이 {len(found)}건이라 원본을 특정할 수 없음")
    return found[0]


def analyze_transcript(transcript: Sequence[Mapping], runner: Callable[[str], Union[str, dict]],
                       speaker_map: Optional[Mapping] = None) -> dict:
    """Transcript → MeetingAnalysis. 검증을 통과한 결과만 반환한다."""
    raw = runner(build_prompt(transcript, speaker_map))
    try:
        analysis = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        raise AnalysisValidationError(f"runner 출력이 JSON이 아님: {e}") from e
    validate_analysis(analysis, transcript)
    return analysis


def save_analysis(analysis, transcript: Sequence[Mapping], meetings_dir) -> Path:
    """검증을 통과한 분석만 meetings/<meeting_id>.analysis.json 으로 저장한다."""
    validate_analysis(analysis, transcript)
    path = Path(meetings_dir) / f"{analysis['meeting_id']}.analysis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
