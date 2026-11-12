"""T-02-1: Gemini Audio STT Provider (외부 API 호출 없음 — call 주입)."""
import json
from pathlib import Path

import pytest

from meeting_action_agent.stt import run_stt
from meeting_action_agent.stt.base import SttError, SttProvider
from meeting_action_agent.stt.gemini_stt import STT_PROMPT, GeminiSttProvider, parse_response

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "stt"
SAMPLE = (FIX / "gemini_response_sample.json").read_text(encoding="utf-8")


def make(tmp_path, ext=".wav", payload=b"RIFF....WAVE-data"):
    p = tmp_path / f"a{ext}"
    p.write_bytes(payload)
    return p


def test_provider_follows_protocol():
    assert isinstance(GeminiSttProvider(call=lambda *a: SAMPLE), SttProvider)


def test_transcribe_passes_audio_mime_and_prompt(tmp_path):
    seen = {}

    def fake(audio, mime, prompt):
        seen.update(audio=audio, mime=mime, prompt=prompt)
        return SAMPLE

    p = make(tmp_path, ".m4a", b"\x00\x00\x00\x18ftypM4A ")
    segs = GeminiSttProvider(call=fake).transcribe(p)
    assert seen["audio"] == p.read_bytes() and seen["mime"] == "audio/mp4" and seen["prompt"] == STT_PROMPT
    assert len(segs) == 8 and set(segs[0]) == {"speaker", "timestamp", "text"}


@pytest.mark.parametrize("ext,mime", [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".WAV", "audio/wav"), (".m4a", "audio/mp4")])
def test_mime_types(tmp_path, ext, mime):
    seen = {}

    def fake(a, m, p):
        seen["m"] = m
        return SAMPLE

    GeminiSttProvider(call=fake).transcribe(make(tmp_path, ext))
    assert seen["m"] == mime


def test_unsupported_extension_rejected(tmp_path):
    with pytest.raises(SttError):
        GeminiSttProvider(call=lambda *a: SAMPLE).transcribe(make(tmp_path, ".ogg"))


def test_too_large_for_inline_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("meeting_action_agent.stt.gemini_stt.MAX_INLINE_BYTES", 4)
    called = []
    with pytest.raises(SttError, match="크기"):
        GeminiSttProvider(call=lambda *a: called.append(1)).transcribe(make(tmp_path))
    assert not called


def test_prompt_enforces_verbatim_and_label_policy():
    for needle in ("원문", "요약", "speaker_1", "이름", "timestamp"):
        assert needle in STT_PROMPT


def test_prompt_does_not_ask_for_analysis():
    """STT 책임은 Audio → Transcript까지. Action 추출·분석을 요청하지 않는다."""
    assert "추출해" not in STT_PROMPT and "Action을 " not in STT_PROMPT


def test_parse_accepts_object_or_list():
    segs = json.loads(SAMPLE)["segments"]
    assert parse_response(json.dumps({"segments": segs})) == segs
    assert parse_response(json.dumps(segs)) == segs


def test_parse_drops_unknown_fields_but_keeps_text_untouched():
    out = parse_response(json.dumps({"segments": [{"speaker": "speaker_1", "timestamp": "00:01", "text": "가", "confidence": 0.2}]}))
    assert out == [{"speaker": "speaker_1", "timestamp": "00:01", "text": "가"}]


@pytest.mark.parametrize("bad", [
    "not json", "{}", "[]", '{"segments": []}', '{"segments": [{"speaker": "s"}]}', '{"segments": [1]}',
    '{"segments": [{"speaker": "speaker_1", "timestamp": 5, "text": "x"}]}',
])
def test_parse_rejects_malformed(bad):
    with pytest.raises(SttError):
        parse_response(bad)


def test_provider_error_is_wrapped(tmp_path):
    def boom(*a):
        raise RuntimeError("network down")

    with pytest.raises(SttError, match="network down"):
        GeminiSttProvider(call=boom).transcribe(make(tmp_path))


def test_run_stt_end_to_end_with_fixture(tmp_path):
    t = run_stt(GeminiSttProvider(call=lambda *a: SAMPLE), make(tmp_path), "20261110-01",
                speaker_map={"speaker_2": {"name": "김대리"}})
    assert t[1]["speaker"] == "김대리" and t[0]["speaker"] == "speaker_1" and t[2]["timestamp"] == "00:00:12"
