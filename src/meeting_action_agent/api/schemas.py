from typing import List, Literal, Optional

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
    status: str
    assignee: str
    assigner: str
    task: str
    due_date: Optional[str]
    due_date_normalized: Optional[str]
    flags: List[str]
    evidence: Evidence


class RejectedView(BaseModel):
    task: str
    assignee: str
    timestamp: str
    evidence: str


class AnalysisResult(BaseModel):
    meeting_id: str
    status: Literal["analyzed"]
    mode: Literal["live", "demo"]
    utterance_count: int
    summary: List[SummaryItem]
    decisions: List[SummaryItem]
    open_issues: List[SummaryItem]
    needs_confirmation: List[SummaryItem]
    actions: List[ActionView]
    rejected: List[RejectedView]
