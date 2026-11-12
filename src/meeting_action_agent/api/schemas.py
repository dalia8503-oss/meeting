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


class MeetingSummary(BaseModel):
    meeting_id: str
    title: str
    meeting_date: str
    created_at: Optional[str] = None      # 등록 시각(audio.json recorded_at). 기록이 없으면 null
    analysis_status: Literal["uploaded", "analyzed"]
    action_count: int


class MeetingList(BaseModel):
    meetings: List[MeetingSummary]


class SummaryItem(BaseModel):
    text: str
    evidence_timestamps: List[str]


class Evidence(BaseModel):
    timestamp: str
    speaker: Optional[str] = None       # 원본 STT 화자 라벨(speaker_N). 이름으로 바꾸지 않는다.
    speaker_name: Optional[str] = None  # 사용자가 확인한 이름 (보조 표시). 미확인이면 null
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


class SpeakerSample(BaseModel):
    timestamp: str
    text: str


class SpeakerItem(BaseModel):
    speaker: str                        # 원본 라벨 (speaker_N)
    utterance_count: int
    samples: List[SpeakerSample]        # transcript의 실제 발언 원문 (생성·요약 없음)
    confirmed_name: Optional[str] = None  # 사용자가 확인한 이름. 없으면 미확인


class SpeakerList(BaseModel):
    meeting_id: str
    speakers: List[SpeakerItem]
    confirmed: bool                     # 사용자가 화자 확인을 한 번이라도 저장했는가


class SpeakerMapRequest(BaseModel):
    """{"speaker_1": "팀장", "speaker_2": null}. null = 미확인(저장하지 않음). 빈 문자열은 거부된다."""
    speaker_map: Dict[str, Optional[str]]


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


# ---- 지난 회의 브리핑 (T-BRIEF-API+TTS)
class BriefingRequest(BaseModel):
    previous_meeting_id: str            # 서버가 직전 회의를 추측하지 않는다. 호출자가 명시한다.
    current_date: str                   # YYYY-MM-DD. 시스템 날짜를 암묵적으로 쓰지 않는다.
    regenerate: bool = False            # true일 때만 저장된 브리핑을 버리고 새로 생성한다.


class BriefingAudio(BaseModel):
    format: Literal["wav"]
    media_type: str
    duration_sec: float
    size_bytes: int
    url: str                            # GET으로 재생 가능한 audio endpoint


class BriefingResponse(BaseModel):
    status: Literal["approved"]
    briefing_id: str
    previous_meeting_id: str
    current_date: str
    briefing_text: str
    referenced_action_ids: List[str]
    reviewer_verdict: Literal["PASS"]
    audio: BriefingAudio
    reused: bool                        # true면 저장된 승인 브리핑을 재사용(LLM 호출 없음)
