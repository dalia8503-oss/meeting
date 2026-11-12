from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    mode: Literal["live", "demo"]
    gemini_configured: bool


class MeetingCreated(BaseModel):
    meeting_id: str
    title: str
    meeting_date: str
    audio_filename: str
    status: Literal["uploaded"]


class SummaryItem(BaseModel):
    text: str
    evidence_timestamps: List[str]


class Evidence(BaseModel):
    timestamp: str
    speaker: Optional[str] = None
    text: str


class ActionView(BaseModel):
    action_id: str
    meeting_id: Optional[str] = None
    status: str
    assignee: str
    assigner: str
    task: str
    due_date: Optional[str]
    due_date_normalized: Optional[str]
    flags: List[str]
    evidence: Evidence
    approved_by_human: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = []


class RejectedView(BaseModel):
    task: str
    assignee: str
    timestamp: str
    evidence: str


class AnalyzeRequest(BaseModel):
    """모두 선택 사항. 본문이 없어도 된다. 시리즈 자동 추론은 없다 — 이전 회의는 사용자가 명시한다."""
    previous_meeting_ids: Optional[List[str]] = None


class MeetingAnalysisView(BaseModel):
    summary: List[SummaryItem]
    decisions: List[SummaryItem]
    open_issues: List[SummaryItem]
    needs_confirmation: List[SummaryItem]


class FollowupResultView(BaseModel):
    action_id: str
    outcome: str                 # applied | reassessed | rejected | skipped
    from_status: Optional[str]
    to_status: Optional[str]
    detail: str
    reviewer_verdict: Optional[str] = None
    evidence: List[Evidence] = []


class AgentRunView(BaseModel):
    agent: str
    status: str                  # ok | error
    started_at: float            # 분석 시작 기준 상대 초 (thread 정보는 노출하지 않는다)
    finished_at: float
    reviewer: Dict[str, int]
    error: Optional[str] = None


class ExecutionView(BaseModel):
    mode: Literal["sequential", "parallel"]
    overlapped: bool
    agents: List[AgentRunView]
    reviewer: Dict[str, int]     # 전체 Reviewer 판정 집계


class AnalysisResult(BaseModel):
    meeting_id: str
    status: Literal["analyzed"]
    mode: Literal["live", "demo"]
    utterance_count: int
    summary: List[SummaryItem]
    decisions: List[SummaryItem]
    open_issues: List[SummaryItem]
    needs_confirmation: List[SummaryItem]
    actions: List[ActionView]        # 이번 회의 Action + 이행점검 대상 이전 Action의 최종 상태
    rejected: List[RejectedView]
    meeting_analysis: Optional[MeetingAnalysisView] = None
    new_actions: List[ActionView] = []
    followup_results: List[FollowupResultView] = []
    followup_invalid: List[Dict[str, Any]] = []
    previous_meeting_ids: List[str] = []
    execution: Optional[ExecutionView] = None
    new_actions_error: Optional[str] = None
    followup_error: Optional[str] = None


class ReviewRequest(BaseModel):
    choice: str
    actor_name: str
    meeting_id: Optional[str] = None
    timestamp: Optional[str] = None


class WithdrawRequest(BaseModel):
    actor_name: str
