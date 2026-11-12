"""T-00-13: 대본(script.json) → TTS(Windows SAPI ko-KR) → 발언 사이 무음을 넣어 이어붙인 wav + answer.json.

실행: python scripts/make_tts_fixture.py   (Windows + Microsoft Heami 음성 필요, 외부 API·네트워크 없음)
answer.json의 timestamp는 이어붙인 오디오의 각 발언 시작 위치(경과시간)로 계산한 정답이다.
"""
import json
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "stt"
NAME = "tts_meeting_001"
GAP_SEC = 0.6


def hhmmss(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main() -> None:
    script = json.loads((FIX / f"{NAME}.script.json").read_text(encoding="utf-8"))
    ps1 = ROOT / "scripts" / "tts_synth.ps1"
    answer_utts, frames, params, cursor = [], [], None, 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for i, u in enumerate(script["utterances"]):
            txt, wav = Path(tmp) / f"{i}.txt", Path(tmp) / f"{i}.wav"
            txt.write_text(u["text"], encoding="utf-8")
            v = script["voices"][u["speaker"]]
            subprocess.run(["powershell", "-NoProfile", "-File", str(ps1), "-TextFile", str(txt),
                            "-OutWav", str(wav), "-Pitch", v["pitch"], "-Rate", v["rate"]], check=True)
            with wave.open(str(wav)) as w:
                params = w.getparams()
                data = w.readframes(w.getnframes())
                dur = w.getnframes() / w.getframerate()
            answer_utts.append({"speaker": u["speaker"], "timestamp": hhmmss(cursor),
                                "start_sec": round(cursor, 2), "text": u["text"]})
            gap = b"\x00" * int(params.framerate * GAP_SEC) * params.sampwidth * params.nchannels
            frames += [data, gap]
            cursor += dur + GAP_SEC
    out = FIX / f"{NAME}.wav"
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    answer = {k: v for k, v in script.items() if k not in ("utterances", "voices", "_comment")}
    answer.update({"audio": out.name, "duration_sec": round(cursor, 2), "utterances": answer_utts})
    (FIX / f"{NAME}.answer.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.name} ({cursor:.1f}s) and {NAME}.answer.json")


if __name__ == "__main__":
    main()
