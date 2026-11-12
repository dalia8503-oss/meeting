"""T-BRIEF-API+TTS: TTS abstraction. synthesize(text) -> AudioResult.

- 입력은 검증을 통과한 briefing_text 문자열뿐이다. 이 계층은 텍스트를 수정하지 않는다.
- MVP provider는 로컬 Windows SAPI(System.Speech, ko-KR 음성)다. 외부 cloud TTS로 텍스트를 보내지 않는다.
- provider 교체는 `synthesize(text) -> AudioResult`를 가진 객체로 한다. 실제 음성 합성은 injected `run`으로 대체 가능(테스트).
- 출력 형식은 브라우저가 재생 가능한 WAV(16kHz mono 16bit)다. 새 dependency 없음.
"""
import io
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SAMPLE_RATE = 16000
SYNTH_TIMEOUT_SEC = 120

_PS_SCRIPT = r"""
param([string]$TextFile, [string]$OutWav)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$ko = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1
if (-not $ko) { [Console]::Error.WriteLine('NO_KOREAN_VOICE'); exit 3 }
$s.SelectVoice($ko.VoiceInfo.Name)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($OutWav, $fmt)
$s.Speak($text)
$s.Dispose()
"""


class TtsError(Exception):
    """음성 합성 실패. 메시지에 텍스트 내용을 담지 않는다."""


@dataclass(frozen=True)
class AudioResult:
    data: bytes
    media_type: str       # 예: audio/wav
    duration_sec: float


def make_wav(pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def wav_duration_sec(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate = w.getframerate()
            if rate <= 0:
                raise TtsError("WAV 샘플레이트가 올바르지 않음")
            return w.getnframes() / rate
    except (wave.Error, EOFError) as e:
        raise TtsError("재생 가능한 WAV가 아님") from e


def _run_powershell_sapi(text: str, out_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="maa_tts_") as tmp:
        txt, ps1 = Path(tmp) / "in.txt", Path(tmp) / "synth.ps1"
        txt.write_text(text, encoding="utf-8")
        ps1.write_text(_PS_SCRIPT, encoding="utf-8-sig")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
             "-TextFile", str(txt), "-OutWav", str(out_path)],
            capture_output=True, timeout=SYNTH_TIMEOUT_SEC)
    if p.returncode == 3:
        raise TtsError("설치된 한국어(ko-KR) Windows 음성이 없음")
    if p.returncode != 0:
        raise TtsError(f"Windows TTS 실행 실패(exit {p.returncode})")


class WindowsSapiTts:
    """로컬 Windows SAPI TTS. 텍스트는 로컬 프로세스에만 전달되고 외부로 나가지 않는다."""

    def __init__(self, run: Optional[Callable[[str, Path], None]] = None):
        self._run = run or _run_powershell_sapi

    def synthesize(self, text: str) -> AudioResult:
        if not isinstance(text, str) or not text.strip():
            raise TtsError("합성할 텍스트가 비어 있음")
        if self._run is _run_powershell_sapi and sys.platform != "win32":
            raise TtsError("Windows 환경에서만 사용할 수 있는 TTS provider")
        with tempfile.TemporaryDirectory(prefix="maa_tts_out_") as tmp:
            out = Path(tmp) / "out.wav"
            try:
                self._run(text, out)
            except TtsError:
                raise
            except (OSError, subprocess.SubprocessError) as e:
                raise TtsError(f"TTS 실행 오류: {type(e).__name__}") from e
            data = out.read_bytes() if out.is_file() else b""
        if not data:
            raise TtsError("TTS가 빈 오디오를 생성함")
        return AudioResult(data=data, media_type="audio/wav", duration_sec=wav_duration_sec(data))
