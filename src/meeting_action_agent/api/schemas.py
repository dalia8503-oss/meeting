from typing import Any, Dict, List, Literal, Optional, Union

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


class MeetingDeleted(BaseModel):
    deleted: bool
    meeting_id: str


class MeetingList(BaseModel):
    meetings: List[MeetingSummary]


class SummaryItem(BaseModel):
    text: str
    evidence_timestamps: List[str]


class Evidence(BaseModel):
    timestamp: str
    speaker: Optional[str] = None       # 원본 STT 화자 라벨(speaker_N). 이름으로 바꾸지 않는다.
    speaker_name: Optional[str] = None  # 사용자가 확인한 이름 (보조 표시). 미확인이면 null
    speaker_display: Optional[str] = None  # 부/과를 포함한 표시명 (예: "1부 3과 이과장"). 표시 전용
    text: str


class ActionView(BaseModel):
    action_id: str
    meeting_id: Optional[str] = None
    status: str
    assignee: str
    assigner: str
    assignee_display: Optional[str] = None  # assignee가 확인된 화자 이름과 일치할 때만 부/과 포함 표시명 (표시 전용)
    assigner_display: Optional[str] = None
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
    index: int = 0                      # review 기록 안의 순번 (채택 API가 가리키는 값)
    assigner: str = ""
    reasons: List[str] = []             # Reviewer가 제외한 사유 ("코드: 설명")
    adopted_action_ids: List[str] = []  # 사람이 채택해 만들어진 Action (없으면 미채택)
    adopted_by: Optional[str] = None


class SpeakerSample(BaseModel):
    timestamp: str
    text: str


class SpeakerSuggestion(BaseModel):
    """Speaker Identification Agent의 1차 추천. 추정값이며 확정 신원이 아니다 (speaker_map과 별개)."""
    speaker_id: str
    suggested_name: Optional[str] = None
    department: Optional[str] = None    # 녹취에 명시된 경우에만. 아니면 null
    section: Optional[str] = None
    confidence: float
    confidence_level: Literal["high", "medium", "low"]
    evidence: List[str]
    needs_confirmation: bool


class SpeakerSuggestionList(BaseModel):
    meeting_id: str
    suggestions: List[SpeakerSuggestion]


class SpeakerProfile(BaseModel):
    """사람이 확인한 화자 프로필 (department/section은 선택). identity는 name이다."""
    name: str
    department: Optional[str] = None
    section: Optional[str] = None
    display_name: str


class SpeakerProfileInput(BaseModel):
    name: str
    department: Optional[str] = None
    section: Optional[str] = None


class SpeakerItem(BaseModel):
    speaker: str                        # 원본 라벨 (speaker_N)
    utterance_count: int
    samples: List[SpeakerSample]        # transcript의 실제 발언 원문 (생성·요약 없음)
    confirmed_name: Optional[str] = None  # 사용자가 확인한 이름(speaker_map). 없으면 미확인
    confirmed_profile: Optional[SpeakerProfile] = None  # 확인된 부/과/이름/표시명
    suggestion: Optional[SpeakerSuggestion] = None  # AI 추천(미확정). confirmed_name과 별개


class SpeakerList(BaseModel):
    meeting_id: str
    speakers: List[SpeakerItem]
    confirmed: bool                     # 사용자가 화자 확인을 한 번이라도 저장했는가
    analyzed: bool = False              # 이미 분석된 회의인가
    stale: bool = False                 # 분석 후 확정 화자가 바뀌어 기존 분석이 최신이 아닌가


class SpeakerMapRequest(BaseModel):
    """{"speaker_1": "팀장", "speaker_2": null}. null = 미확인(저장하지 않음). 빈 문자열은 거부된다."""
    speaker_map: Dict[str, Optional[Union[str, SpeakerProfileInput]]]


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


class TranscriptLine(BaseModel):
    """원본 transcript 한 줄 (읽기 전용 표시용). speaker는 원본 라벨이며 이름으로 바꾸지 않는다."""
    timestamp: str
    speaker: str
    speaker_name: Optional[str] = None  # 사람이 확인한 이름 (보조 표시)
    speaker_display: Optional[str] = None  # 부/과 포함 표시명
    speaker_department: Optional[str] = None
    speaker_section: Optional[str] = None
    text: str


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
    transcript: List[TranscriptLine] = []
    stale: bool = False                 # 분석 이후 화자 확인이 바뀌었다 → 이 결과는 최신이 아니며 재분석이 필요하다
    speaker_map_used: Dict[str, str] = {}  # 이 분석이 사용한 확정 화자 {speaker_N: 이름}
    # 사람이 "회의 분석 확정"을 누른 상태. stale이면 항상 False (화자 변경 후에는 다시 확정해야 한다).
    # 확정은 화면 흐름 표시일 뿐이며 Action 상태·Reviewer 판정·완료 승인에는 영향을 주지 않는다.
    analysis_finalized: bool = False
    analysis_finalized_at: Optional[str] = None


class AdoptRequest(BaseModel):
    """Reviewer가 제외한 후보를 사람이 Action으로 채택한다. 채택자 이름은 필수다 (승인과 같은 책임 기록)."""
    actor_name: str
    assignees: Optional[List[str]] = None  # 담당자 지정·수정(여러 명이면 Action이 담당자별로 생긴다). 후보 값이 화자 라벨이면 필수
    assigner: Optional[str] = None


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
