"""T-11 Multi-Agent Orchestrator — "누구에게 어떤 일을 시키고 결과를 어떤 순서로 합칠 것인가"만 담당한다.

판정·검증·상태전이·담당자·날짜·retry·Reviewer 규칙은 기존 함수가 수행한다(복사 없음).

  1차 회의(이전 Action 없음): Meeting Analyst → Reviewer → Action Manager   (기존 순차 구조 그대로)

  2차 회의(이전 Action 있음):
      ┌ Branch A (thread): Meeting Analyst → Reviewer          ← analyze_and_review   (read-only)
      └ Branch B (thread): Follow-up Agent → Reviewer(+retry)  ← check_previous_actions
                                                                  + prepare_followups_with_retry (read-only)
      ── join ──
      Action Manager (main thread, 순차): Follow-up 반영 → 신규 Action 생성   (store write는 여기서만)

동시 write 방지: 병렬 구간(분석·검증)은 store를 읽기만 한다. 같은 파일에 쓰는 단계는 join 이후 단일 스레드다.

실패 정책
- Branch A의 예상된 도메인 실패(분석 계약 위반·Reviewer 출력 invalid 등)는 new_actions_error로 격리하고
  Branch B 결과는 유지한다. Branch B의 Action 단위 실패(invalid·REJECT·Reviewer 출력 invalid)도 그 Action만 격리된다.
- Branch B의 점검 단위 실패(transcript 계약 위반)는 followup_error로 격리한다(write 없음, 신규 Action은 계속).
- 안전 위반(Agent '완료' 제안=CompletionGuardError), snapshot 충돌(FileExistsError), 프로그램 오류·RuntimeError 등은
  삼키지 않는다. join 이후 어떤 store write도 일어나기 전에 전파되므로 기존 fail-closed가 유지된다.
- Human Review(review_action)는 Orchestrator 밖이다. Agent는 '완료 후보'까지만 만든다.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.analyst import AnalysisValidationError
from meeting_action_agent.contracts import SchemaValidationError
from meeting_action_agent.manager import ConversionContextError
from meeting_action_agent.manager.followup import (
    FollowupValidationError, NoPreviousActionsSpecified, check_previous_actions)
from meeting_action_agent.manager.followup_apply import commit_prepared_followups, prepare_followups_with_retry
from meeting_action_agent.pipeline import (
    MeetingPipelineResult, analyze_and_review, convert_reviewed, extract_review_convert)
from meeting_action_agent.reviewer import ReviewOutputError
from meeting_action_agent.state import CompletionGuardError

Runner = Callable[[str], Union[str, dict]]

# Branch A·새 Action 경로에서 격리하는 예상된 도메인 실패 (run_meeting_pipeline과 동일한 목록)
_NEW_PATH_DOMAIN_ERRORS = (AnalysisValidationError, SchemaValidationError, ConversionContextError,
                           ReviewOutputError, CompletionGuardError)
# Branch B 점검 단위의 예상된 도메인 실패. CompletionGuardError(안전 위반)는 여기 넣지 않는다.
_FOLLOWUP_DOMAIN_ERRORS = (FollowupValidationError, SchemaValidationError)


@dataclass(frozen=True)
class AgentRun:
    """Agent 1회 실행 기록 (교육·Demo용 최소 metadata). 시각은 time.perf_counter() 초(단조 증가)다."""
    agent: str
    started_at: float
    finished_at: float
    thread: str
    status: str = "ok"                 # ok | error(격리된 도메인 실패)
    reviewer: Mapping = field(default_factory=dict)   # Reviewer 판정 집계 {"PASS": n, "REJECT": m}
    error: Optional[str] = None


@dataclass(frozen=True)
class OrchestrationResult(MeetingPipelineResult):
    """MeetingPipelineResult(follow-up·신규 Action 결과)에 실행 metadata를 더한 결과. 필드는 그대로 재사용한다."""
    mode: str = "sequential"           # sequential(1차 회의) | parallel(2차 회의)
    execution: tuple = ()              # AgentRun들
    followup_error: Optional[str] = None

    @property
    def overlapped(self) -> bool:
        """두 Branch의 실행 구간이 실제로 겹쳤는가."""
        if len(self.execution) != 2:
            return False
        a, b = self.execution
        return a.started_at < b.finished_at and b.started_at < a.finished_at


def _tally(verdicts) -> dict:
    out: dict = {}
    for v in verdicts:
        out[v] = out.get(v, 0) + 1
    return out


def _timed(agent: str, fn: Callable[[], tuple]) -> tuple:
    """fn() -> (value, reviewer 집계, error|None). 예상 밖 예외는 그대로 전파된다."""
    started, thread = time.perf_counter(), _thread_name()
    value, reviewer, error = fn()
    run = AgentRun(agent, started, time.perf_counter(), thread, "error" if error else "ok", reviewer, error)
    return value, run


def _thread_name() -> str:
    import threading
    return threading.current_thread().name


def _has_previous_actions(store, transcript: Sequence[Mapping], previous_meeting_ids=None) -> bool:
    if previous_meeting_ids is not None and len(previous_meeting_ids) == 0:
        return False  # 이전 회의를 명시적으로 지정하지 않음(빈 목록): 신규 회의 분석만 한다. None은 기존 동작(전체) 유지.
    current = {u["meeting_id"] for u in transcript}
    return any(a["meeting_id"] not in current for a in store.load_all())


def run_orchestrated_meeting(
    transcript: Sequence[Mapping],
    *,
    analyst_runner: Runner,
    candidate_reviewer_runner: Runner,
    followup_runner: Runner,
    followup_reviewer_runner: Runner,
    store,
    metadata: Mapping,
    previous_meeting_ids: Optional[Sequence[str]] = None,
    meetings_dir=None,
) -> OrchestrationResult:
    """run_meeting_pipeline과 같은 입력. 2차 회의의 두 독립 Branch를 동시에 분석·검증한 뒤 순차 반영한다."""
    if not _has_previous_actions(store, transcript, previous_meeting_ids):
        return _run_first_meeting(transcript, analyst_runner, candidate_reviewer_runner,
                                  store=store, metadata=metadata)

    def branch_a():
        try:
            reviewed = analyze_and_review(transcript, analyst_runner, candidate_reviewer_runner)
        except _NEW_PATH_DOMAIN_ERRORS as e:
            return None, {}, f"{type(e).__name__}: {e}"
        return reviewed, _tally(r["verdict"] for r in reviewed.reviews), None

    def branch_b():
        try:
            report = check_previous_actions(transcript, followup_runner, store=store,
                                            previous_meeting_ids=previous_meeting_ids, meetings_dir=meetings_dir)
        except NoPreviousActionsSpecified:
            return ((), (), None), {}, None
        except _FOLLOWUP_DOMAIN_ERRORS as e:
            return None, {}, f"{type(e).__name__}: {e}"
        prepared = prepare_followups_with_retry(store, report.results, transcript, followup_runner,
                                                followup_reviewer_runner)
        verdicts = (p.review["verdict"] for p in prepared if p.review)
        return (prepared, tuple(report.rejected), report.saved_path), _tally(verdicts), None

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent") as pool:
        fa = pool.submit(_timed, "meeting-analyst", branch_a)
        fb = pool.submit(_timed, "followup-agent", branch_b)
        (reviewed, run_a), (b_value, run_b) = fa.result(), fb.result()  # 예상 밖 예외는 여기서 전파(write 없음)

    # ---- join 이후: Action Manager. store write는 단일 스레드, 순서 고정 (Follow-up → 신규) ----
    outcomes, invalid, snapshot = [], (), None
    if b_value is not None:
        prepared, invalid, snapshot = b_value
        outcomes = commit_prepared_followups(store, prepared, metadata["meeting_date"])

    new, new_error = None, run_a.error
    if reviewed is not None:
        try:
            new = convert_reviewed(reviewed, store=store, metadata=metadata)
        except _NEW_PATH_DOMAIN_ERRORS as e:
            new_error = f"{type(e).__name__}: {e}"

    return OrchestrationResult(
        tuple(outcomes), tuple(o for o in outcomes if o.outcome == "rejected"), tuple(invalid), new, new_error,
        snapshot, mode="parallel", execution=(run_a, run_b), followup_error=run_b.error)


def _run_first_meeting(transcript, analyst_runner, reviewer_runner, *, store, metadata) -> OrchestrationResult:
    """이전 Action이 없는 회의: 기존 검증된 순차 구조(extract_review_convert)를 그대로 쓴다. 병렬화하지 않는다."""
    started, new, error = time.perf_counter(), None, None
    try:
        new = extract_review_convert(transcript, analyst_runner, reviewer_runner, store=store, metadata=metadata)
    except _NEW_PATH_DOMAIN_ERRORS as e:
        error = f"{type(e).__name__}: {e}"
    run = AgentRun("meeting-analyst", started, time.perf_counter(), _thread_name(),
                   "error" if error else "ok", _tally(r["verdict"] for r in new.reviews) if new else {}, error)
    return OrchestrationResult((), (), (), new, error, None, mode="sequential", execution=(run,))
