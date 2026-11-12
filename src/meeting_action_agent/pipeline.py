"""Vertical slice 연결 (T-05-2/3/8): Transcript → MeetingAnalysis → 원본 Utterance 재연결 → convert_candidate.

새 규칙을 만들지 않는다. 담당자·기한·status·중복·저장은 모두 convert_candidate가 수행한다.
이 파일의 추가 역할(T-05-8)은 두 가지뿐이다.
- 같은 발언·같은 업무로 담당자만 다른 후보들을 묶어 mentions로 넘긴다 (인원별 Action·`공동` flag는 Manager가 처리).
- 후보의 extra_evidence(여러 발언에 걸친 근거)를 원본 Utterance로 재연결한다 (speaker/text 추측 금지).
"""
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.analyst import AnalysisValidationError, analyze_transcript, relink_utterance
from meeting_action_agent.contracts import SchemaValidationError
from meeting_action_agent.manager import ConversionContextError, ConversionResult, convert_candidate
from meeting_action_agent.manager.followup import NoPreviousActionsSpecified, check_previous_actions
from meeting_action_agent.manager.followup_apply import apply_followups_with_retry
from meeting_action_agent.reviewer import ReviewOutputError, review_candidate
from meeting_action_agent.state import CompletionGuardError


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
    analysis: Optional[dict] = None  # 검증을 통과한 MeetingAnalysis (표시·저장용, 수정 없음)


def _review_groups(transcript, analysis, reviewer_runner):
    """후보 그룹별 Reviewer 판정(read-only, lazy). (group, candidate, utterance, verdicts)를 낸다."""
    for group in _group(analysis["action_candidates"]):
        candidate = _relink_extra(group[0], transcript)
        utterance = relink_utterance(candidate, transcript)
        verdicts = [review_candidate(_relink_extra(c, transcript), utterance, reviewer_runner) for c in group]
        yield group, candidate, utterance, verdicts


def _convert_group(group, candidate, utterance, *, store, metadata):
    mentions = list(dict.fromkeys(c["assignee"] for c in group)) if len(group) > 1 else None
    return convert_candidate(candidate, utterance=utterance, mentions=mentions, metadata=metadata,
                             reviewer_passed=True, store=store)


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
    for group, candidate, utterance, verdicts in _review_groups(transcript, analysis, reviewer_runner):
        reviews.extend(verdicts)
        if any(v["verdict"] == "REJECT" for v in verdicts):
            rejected.extend(group)
            continue
        outcomes.extend(_convert_group(group, candidate, utterance, store=store, metadata=metadata).outcomes)
    return ReviewedResult(ConversionResult(tuple(outcomes)), tuple(reviews), tuple(rejected), analysis)


@dataclass(frozen=True)
class ReviewedAnalysis:
    """Analyst → Reviewer까지만 끝난 read-only 산출물. store를 건드리지 않아 다른 Branch와 동시에 만들 수 있다."""
    analysis: dict
    approved: tuple   # (group, candidate, utterance) — Reviewer PASS만
    reviews: tuple
    rejected: tuple


def analyze_and_review(transcript: Sequence[Mapping], analyst_runner, reviewer_runner) -> ReviewedAnalysis:
    """store 없이 분석·검증만 한다 (Orchestrator의 병렬 구간)."""
    analysis = analyze_transcript(transcript, analyst_runner)
    approved, reviews, rejected = [], [], []
    for group, candidate, utterance, verdicts in _review_groups(transcript, analysis, reviewer_runner):
        reviews.extend(verdicts)
        if any(v["verdict"] == "REJECT" for v in verdicts):
            rejected.extend(group)
        else:
            approved.append((group, candidate, utterance))
    return ReviewedAnalysis(analysis, tuple(approved), tuple(reviews), tuple(rejected))


def convert_reviewed(reviewed: ReviewedAnalysis, *, store, metadata: Mapping) -> ReviewedResult:
    """검증된 후보만 순서대로 convert_candidate (store write는 이 단계에서만, 단일 스레드)."""
    outcomes = []
    for group, candidate, utterance in reviewed.approved:
        outcomes.extend(_convert_group(group, candidate, utterance, store=store, metadata=metadata).outcomes)
    return ReviewedResult(ConversionResult(tuple(outcomes)), reviewed.reviews, reviewed.rejected, reviewed.analysis)


@dataclass(frozen=True)
class MeetingPipelineResult:
    """이행점검 + 신규 Action 결과. 두 경로는 독립적이다."""
    followup_outcomes: tuple = ()    # ApplyOutcome 전부 (applied | reassessed | rejected | skipped)
    followup_rejected: tuple = ()    # 그중 최종 rejected (Action 불변)
    followup_invalid: tuple = ()     # 첫 판정 검증 실패 {action_id, reason} (Reviewer 이전, Action 불변)
    new_actions: Optional[ReviewedResult] = None   # 신규 Action 경로 결과 (추출 실패 시 None)
    new_actions_error: Optional[str] = None        # 신규 경로 실패 사유 (follow-up 반영은 이미 유지됨)
    snapshot_path: Optional[object] = None


def run_meeting_pipeline(
    transcript: Sequence[Mapping],
    *,
    analyst_runner: Callable[[str], Union[str, dict]],
    candidate_reviewer_runner: Callable[[str], Union[str, dict]],
    followup_runner: Callable[[str], Union[str, dict]],
    followup_reviewer_runner: Callable[[str], Union[str, dict]],
    store,
    metadata: Mapping,
    previous_meeting_ids: Optional[Sequence[str]] = None,
    meetings_dir=None,
) -> MeetingPipelineResult:
    """T-09-11. Orchestration만 한다 — 판정·검증·상태전이·flag는 기존 함수가 수행한다.

    1) check_previous_actions: 이전 Action 조회·대상 선택·Follow-up 분석·검증(+ meetings_dir면 snapshot create-only)
    2) apply_followups_with_retry: Reviewer → REJECT 시 1회 재분석 → 최종 PASS만 반영 (Action별 독립)
    3) extract_review_convert: 현재 회의의 신규 지시 추출 → Reviewer → 새 Action
    Follow-up이 먼저인 이유: 점검 대상(다른 회의 Action)은 2)에서만 바뀌고, 3)이 만드는 현재 회의 Action은
    점검 대상이 아니라 순서 의존은 없다. 점검 실패(안전 위반·snapshot 충돌)는 어떤 write보다 먼저 발생해
    신규 Action도 만들지 않는다(fail-closed).
    신규 경로의 실패는 이미 반영된 Follow-up 결과를 되돌리지 않고 new_actions_error로 보고한다
    (회의 전체 transaction은 범위 밖 — 기존 TODO).
    """
    try:
        report = check_previous_actions(transcript, followup_runner, store=store,
                                        previous_meeting_ids=previous_meeting_ids, meetings_dir=meetings_dir)
        outcomes = apply_followups_with_retry(store, report.results, transcript, followup_runner,
                                              followup_reviewer_runner, metadata["meeting_date"])
        invalid, snapshot = tuple(report.rejected), report.saved_path
    except NoPreviousActionsSpecified:  # 첫 회의: 점검할 이전 Action이 없다
        outcomes, invalid, snapshot = [], (), None

    new, error = None, None
    try:
        new = extract_review_convert(transcript, analyst_runner, candidate_reviewer_runner,
                                     store=store, metadata=metadata)
    except (AnalysisValidationError, SchemaValidationError, ConversionContextError,
            ReviewOutputError, CompletionGuardError) as e:
        error = f"{type(e).__name__}: {e}"
    return MeetingPipelineResult(
        tuple(outcomes), tuple(o for o in outcomes if o.outcome == "rejected"), invalid, new, error, snapshot)
