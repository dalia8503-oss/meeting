"""Vertical slice 연결 (T-05-2/3/8): Transcript → MeetingAnalysis → 원본 Utterance 재연결 → convert_candidate.

새 규칙을 만들지 않는다. 담당자·기한·status·중복·저장은 모두 convert_candidate가 수행한다.
이 파일의 추가 역할(T-05-8)은 두 가지뿐이다.
- 같은 발언·같은 업무로 담당자만 다른 후보들을 묶어 mentions로 넘긴다 (인원별 Action·`공동` flag는 Manager가 처리).
- 후보의 extra_evidence(여러 발언에 걸친 근거)를 원본 Utterance로 재연결한다 (speaker/text 추측 금지).
"""
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, Union

from meeting_action_agent.analyst import AnalysisValidationError, analyze_transcript, relink_utterance
from meeting_action_agent.manager import ConversionResult, convert_candidate
from meeting_action_agent.reviewer import review_candidate


def _relink_extra(candidate: Mapping, transcript: Sequence[Mapping]) -> dict:
    extra = []
    for q in candidate.get("extra_evidence") or []:
        u = relink_utterance({"meeting_id": q["meeting_id"], "timestamp": q["timestamp"]}, transcript)
        extra.append({"meeting_id": u["meeting_id"], "timestamp": u["timestamp"],
                      "speaker": u["speaker"], "text": u["text"]})
    return {**candidate, "extra_evidence": extra} if extra else dict(candidate)


def _group(items: Sequence[Mapping]) -> list:
    """(meeting_id, timestamp, task)가 같은 후보는 한 발언의 복수 담당자로 본다."""
    groups: dict = {}
    for item in items:
        c = item["candidate"]
        groups.setdefault((c["meeting_id"], c["timestamp"], c["task"]), []).append(c)
    return list(groups.values())


def extract_and_convert(
    transcript: Sequence[Mapping],
    runner: Callable[[str], Union[str, dict]],
    *,
    store,
    metadata: Mapping,
) -> ConversionResult:
    analysis = analyze_transcript(transcript, runner)
    outcomes = []
    for group in _group(analysis["action_candidates"]):
        candidate = _relink_extra(group[0], transcript)
        utterance = relink_utterance(candidate, transcript)  # speaker/text는 원본에서
        mentions = list(dict.fromkeys(c["assignee"] for c in group)) if len(group) > 1 else None
        result = convert_candidate(candidate, utterance=utterance, mentions=mentions, metadata=metadata, store=store)
        outcomes.extend(result.outcomes)
    return ConversionResult(tuple(outcomes))


@dataclass(frozen=True)
class ReviewedResult:
    conversion: ConversionResult
    reviews: tuple    # review-result dict들 (후보 순서)
    rejected: tuple   # REJECT된 후보 dict들 — Action 생성 없음 (재시도·큐 적재는 T-06-7/8)


def extract_review_convert(
    transcript: Sequence[Mapping],
    analyst_runner: Callable[[str], Union[str, dict]],
    reviewer_runner: Callable[[str], Union[str, dict]],
    *,
    store,
    metadata: Mapping,
) -> ReviewedResult:
    """Analyst → Reviewer → (PASS만) convert_candidate. REJECT는 정상 Action 경로로 보내지 않는다."""
    analysis = analyze_transcript(transcript, analyst_runner)
    outcomes, reviews, rejected = [], [], []
    for group in _group(analysis["action_candidates"]):
        candidate = _relink_extra(group[0], transcript)
        utterance = relink_utterance(candidate, transcript)
        verdicts = [
            review_candidate(_relink_extra(c, transcript), utterance, reviewer_runner) for c in group
        ]
        reviews.extend(verdicts)
        if any(v["verdict"] == "REJECT" for v in verdicts):
            rejected.extend(group)
            continue
        mentions = list(dict.fromkeys(c["assignee"] for c in group)) if len(group) > 1 else None
        result = convert_candidate(candidate, utterance=utterance, mentions=mentions, metadata=metadata,
                                   reviewer_passed=True, store=store)
        outcomes.extend(result.outcomes)
    return ReviewedResult(ConversionResult(tuple(outcomes)), tuple(reviews), tuple(rejected))
