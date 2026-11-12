"""수동 smoke (자동 pytest 아님): 실제 Gemini Audio로 TTS 테스트 음성 1개를 전사하고 정답 대본과 비교한다 (T-02-7 최소 검증).

실행: python scripts/smoke_gemini_stt.py   (GEMINI_API_KEY 필요, API 1회 호출)
STT만 확인한다 — Analyst·Reviewer·Action 변환은 호출하지 않는다. 결과는 smoke_results/ (gitignore)에 저장한다.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meeting_action_agent.env import load_env
from meeting_action_agent.stt import build_transcript
from meeting_action_agent.stt.gemini_stt import GeminiSttProvider

FIX = ROOT / "tests" / "fixtures" / "stt"


def _norm(s: str) -> str:
    return re.sub(r"[\s\.,!?~…·'\"]", "", s)


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    return _lev(r, h) / max(len(r), 1)


def _sec(ts: str) -> int:
    h, m, s = map(int, ts.split(":"))
    return h * 3600 + m * 60 + s


def main() -> int:
    load_env()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY 환경변수가 필요합니다.")
    ans = json.loads((FIX / "tts_meeting_001.answer.json").read_text(encoding="utf-8"))
    result = {"model": os.environ.get("GEMINI_STT_MODEL") or os.environ.get("GEMINI_MODEL"), "status": None}
    t0 = time.time()
    try:
        segs = GeminiSttProvider().transcribe(FIX / ans["audio"])
        result["raw_segments"] = segs
        transcript = build_transcript(segs, ans["meeting_id"])
        result["transcript"] = transcript
        ref = " ".join(u["text"] for u in ans["utterances"])
        hyp = " ".join(u["text"] for u in transcript)
        result["cer_total"] = round(cer(ref, hyp), 4)
        result["utterances"] = {"expected": len(ans["utterances"]), "got": len(transcript)}
        result["speakers_got"] = sorted({u["speaker"] for u in transcript})
        result["speakers_expected"] = sorted({u["speaker"] for u in ans["utterances"]})
        if len(transcript) == len(ans["utterances"]):  # 발언 수가 같을 때만 1:1 비교
            result["per_utterance"] = [
                {"expected_speaker": e["speaker"], "got_speaker": g["speaker"],
                 "timestamp_error_sec": _sec(g["timestamp"]) - round(e["start_sec"]), "cer": round(cer(e["text"], g["text"]), 3)}
                for e, g in zip(ans["utterances"], transcript)
            ]
        result["status"] = "ok"
    except Exception as e:
        result["status"] = f"error: {type(e).__name__}: {e}"
    result["elapsed_sec"] = round(time.time() - t0, 2)
    out = ROOT / "smoke_results"
    out.mkdir(exist_ok=True)
    path = out / "gemini_stt_smoke_result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print("saved:", path)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
