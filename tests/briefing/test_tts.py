"""T-BRIEF-API+TTS: TTS abstraction (실제 Windows 음성은 호출하지 않는다)."""
import io
import wave

import pytest

from meeting_action_agent.tts import AudioResult, TtsError, WindowsSapiTts, make_wav, wav_duration_sec


def test_wav_duration_from_header():
    data = make_wav(b"\x00\x00" * 16000, sample_rate=16000)
    assert wav_duration_sec(data) == pytest.approx(1.0)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


def test_invalid_wav_rejected():
    with pytest.raises(TtsError):
        wav_duration_sec(b"not a wav")


def test_sapi_provider_uses_injected_runner_and_returns_audio():
    seen = {}

    def fake_run(text, out_path):
        seen["text"] = text
        out_path.write_bytes(make_wav(b"\x00\x00" * 800))

    r = WindowsSapiTts(run=fake_run).synthesize("안녕하세요")
    assert seen["text"] == "안녕하세요"
    assert isinstance(r, AudioResult) and r.media_type == "audio/wav" and r.duration_sec == pytest.approx(0.05)


def test_sapi_provider_failure_raises_tts_error():
    def boom(text, out_path):
        raise OSError("no voice")

    with pytest.raises(TtsError):
        WindowsSapiTts(run=boom).synthesize("x")


def test_sapi_empty_output_raises():
    with pytest.raises(TtsError):
        WindowsSapiTts(run=lambda t, p: p.write_bytes(b"")).synthesize("x")


def test_empty_text_rejected():
    with pytest.raises(TtsError):
        WindowsSapiTts(run=lambda t, p: None).synthesize("  ")
