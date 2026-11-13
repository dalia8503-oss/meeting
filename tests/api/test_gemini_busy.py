"""Gemini 일시 오류(503/504): 공용 재시도와 사용자 안내 (실제 호출 없음)."""
import pytest

from meeting_action_agent import llm
from meeting_action_agent.api import service

from .test_workflow import base, client, upload  # noqa: F401

errors = pytest.importorskip("google.genai.errors")


def _server_error(code):
    return errors.ServerError(code, {"error": {"code": code, "message": "x", "status": "UNAVAILABLE"}})


class _Client:
    def __init__(self, fails, code=503):
        self.fails, self.code, self.calls = fails, code, 0
        self.models = self

    def generate_content(self, **kw):
        self.calls += 1
        if self.calls <= self.fails:
            raise _server_error(self.code)
        return "ok"


def test_retry_recovers_from_transient_503(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = _Client(fails=2)
    assert llm.generate_with_retry(c, model="m") == "ok" and c.calls == 3


def test_retry_gives_up_after_limit_and_other_codes_are_not_retried(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = _Client(fails=99)
    with pytest.raises(errors.ServerError):
        llm.generate_with_retry(c, model="m")
    assert c.calls == len(llm.GEMINI_RETRY_WAITS) + 1
    c500 = _Client(fails=99, code=500)
    with pytest.raises(errors.ServerError):
        llm.generate_with_retry(c500, model="m")
    assert c500.calls == 1


def test_analyze_maps_busy_gemini_to_503_gemini_busy(client, monkeypatch):  # noqa: F811
    mid = upload(client, "2026-11-06")

    def boom(*a, **k):
        raise _server_error(503)

    monkeypatch.setattr(service, "analyze_meeting", boom)
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "gemini_busy"
