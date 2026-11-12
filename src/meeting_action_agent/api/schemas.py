from typing import Literal

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
