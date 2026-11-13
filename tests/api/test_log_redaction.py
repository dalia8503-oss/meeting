"""로그에 남는 오류 문구에서 키·헤더 값이 가려지는지 (가짜 값만 사용)."""
from meeting_action_agent.api.service import _safe_err

FAKE = "AQ." + "x" * 30


def test_header_value_with_key_is_redacted():
    e = Exception(f"Gemini STT 호출 실패: LocalProtocolError: Illegal header value b'GEMINI_API_KEY={FAKE}\nGEMINI_MODEL=m'")
    out = _safe_err(e)
    assert FAKE not in out and "GEMINI_MODEL" not in out and "Illegal header value" in out


def test_bare_key_and_assignment_are_redacted_and_length_limited():
    assert FAKE not in _safe_err(Exception(f"failed with {FAKE}"))
    assert "AIza" not in _safe_err(Exception("api_key=AIza" + "y" * 30 + " rejected"))
    assert len(_safe_err(Exception("a" * 1000))) == 200


def test_plain_message_is_kept():
    assert _safe_err(Exception("Gemini STT 호출 실패: Timeout")) == "Gemini STT 호출 실패: Timeout"
