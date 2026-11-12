"""T-BRIEF-API+TTS: POST /api/briefings, GET /api/briefings/{id}/audio. LLM·TTS는 모두 fake 주입 (외부 호출 없음)."""
import copy
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from meeting_action_agent.api import briefing_service
from meeting_action_agent.api.app import create_app
from meeting_action_agent.store import ActionStore
from meeting_action_agent.tts import AudioResult, TtsError, make_wav

MID = "20261110-01"
TODAY = "2026-11-12"
TEXT = "지난 회의에서는 도면 수정이 논의되었습니다. 김대리의 업무 A-001은 금요일까지입니다. 담당자 불명확 건은 확인이 필요합니다."


def good(text=TEXT, ids=("A-001",)):
    return {"briefing_text": text,
            "sections": {"previous_summary": "도면 수정 논의", "active_actions": "A-001 금요일까지",
                         "confirmation_items": "담당자 불명확"},
            "referenced_action_ids": list(ids)}


PASS = {"verdict": "PASS", "reasons": []}
REJECT = {"verdict": "REJECT", "reasons": [{"code": "사실생성", "detail": "입력에 없는 사실"}]}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("MAA_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "runtime" / "demo"


def _make_meeting(root, mid=MID, analyzed=True):
    (root / "meetings").mkdir(parents=True, exist_ok=True)
    meta = {"meeting_id": mid, "meeting_date": "2026-11-10", "title": "1차 회의"}
    (root / "meetings" / f"{mid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if analyzed:
        analysis = {"meeting_id": mid,
                    "key_discussions": [{"text": "도면 수정 논의", "evidence_timestamps": ["00:01:00"]}],
                    "decisions": [], "open_issues": [],
                    "needs_confirmation": [{"text": "담당자 불명확", "evidence_timestamps": ["00:04:00"]}],
                    "action_candidates": []}
        (root / "meetings" / f"{mid}.analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def meeting(base, new_action):
    _make_meeting(base)
    store = ActionStore(base / "actions" / "actions.json")
    a = copy.deepcopy(new_action)
    a.update(action_id="A-001", meeting_id=MID, assignee="김대리", task="업무 A-001",
             due_date="금요일까지", due_date_normalized="2026-11-13", status="신규")
    a["history"][0]["to_status"] = "신규"
    store.create(a)
    return base


class Fakes:
    """generator/reviewer/tts 호출을 기록하는 주입 객체."""

    def __init__(self, gens=(good(),), verdicts=(PASS,), tts_fail=0, gen_delay=0.0):
        self.gens, self.verdicts = list(gens), list(verdicts)
        self.events, self.prompts, self.tts_inputs = [], [], []
        self.tts_fail, self.gen_delay = tts_fail, gen_delay

    def generator(self, prompt):
        self.events.append("gen")
        self.prompts.append(prompt)
        time.sleep(self.gen_delay)
        return json.dumps(self.gens[min(self.events.count("gen") - 1, len(self.gens) - 1)], ensure_ascii=False)

    def reviewer(self, prompt):
        self.events.append("rev")
        v = self.verdicts[min(self.events.count("rev") - 1, len(self.verdicts) - 1)]
        return v if isinstance(v, str) else json.dumps(v)

    def tts(self, text):
        self.events.append("tts")
        self.tts_inputs.append(text)
        if self.tts_fail:
            self.tts_fail -= 1
            raise TtsError("fake tts failure")
        return AudioResult(data=make_wav(b"\x00\x00" * 1600), media_type="audio/wav", duration_sec=0.1)


@pytest.fixture
def install(monkeypatch):
    def _install(fakes):
        monkeypatch.setattr(briefing_service, "_runners", lambda: (fakes.generator, fakes.reviewer))
        monkeypatch.setattr(briefing_service, "_tts_provider", lambda: fakes.tts)
        return fakes
    return _install


@pytest.fixture
def client(base):
    return TestClient(create_app())


def post(client, **over):
    body = {"previous_meeting_id": MID, "current_date": TODAY}
    body.update(over)
    return client.post("/api/briefings", json=body)


# ---- 정상 흐름
def test_valid_request_returns_approved_briefing_with_audio(client, meeting, install):
    f = install(Fakes())
    r = post(client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "approved"
    assert b["previous_meeting_id"] == MID and b["current_date"] == TODAY
    assert b["briefing_text"] == TEXT and b["referenced_action_ids"] == ["A-001"]
    assert b["reviewer_verdict"] == "PASS" and b["reused"] is False
    assert b["audio"]["format"] == "wav" and b["audio"]["media_type"] == "audio/wav"
    assert b["audio"]["url"] == f"/api/briefings/{b['briefing_id']}/audio"
    assert b["audio"]["size_bytes"] > 0 and b["audio"]["duration_sec"] > 0
    assert set(b) == {"status", "briefing_id", "previous_meeting_id", "current_date", "briefing_text",
                      "referenced_action_ids", "reviewer_verdict", "audio", "reused"}
    assert f.events == ["gen", "rev", "tts"]


def test_previous_meeting_id_is_required_and_never_guessed(client, meeting, install):
    f = install(Fakes())
    assert client.post("/api/briefings", json={"current_date": TODAY}).status_code == 422
    assert post(client, previous_meeting_id="").status_code in (404, 422)
    assert f.events == []


def test_current_date_is_explicit_and_validated(client, meeting, install):
    f = install(Fakes())
    assert client.post("/api/briefings", json={"previous_meeting_id": MID}).status_code == 422
    assert post(client, current_date="내일").status_code == 422
    assert post(client, current_date="2026-13-45").status_code == 422
    assert f.events == []
    assert post(client, current_date="2026-11-20").status_code == 200
    assert '"current_date": "2026-11-20"' in f.prompts[0]


def test_briefing_input_is_built_from_previous_meeting_actions(client, meeting, install):
    f = install(Fakes())
    post(client)
    p = f.prompts[0]
    assert "업무 A-001" in p and "도면 수정 논의" in p and MID in p


def test_completed_action_is_included_with_approval(client, meeting, install, approval):
    store = ActionStore(meeting / "actions" / "actions.json")
    store.change_status("A-001", "완료 후보", "Agent")
    store.change_status(
        "A-001", "완료", "사람", approved_by_human=approval, actor_name="팀장")
    done = "지난 회의에서는 도면 수정이 논의되었습니다. 김대리의 업무 A-001은 완료되었습니다. 담당자 불명확 건은 확인이 필요합니다."
    f = install(Fakes(gens=[good(text=done, ids=("A-001",))]))
    r = post(client)
    assert r.status_code == 200, r.text
    assert '"completed_actions"' in f.prompts[0] and '"approved_by_human"' in f.prompts[0]


def test_meeting_without_actions(client, base, install):
    _make_meeting(base)
    install(Fakes(gens=[good(text="지난 회의에서는 도면 수정이 논의되었습니다. 담당자 불명확 건은 확인이 필요합니다.", ids=())]))
    r = post(client)
    assert r.status_code == 200 and r.json()["referenced_action_ids"] == []


# ---- 실패 / fail-closed
def test_missing_previous_meeting_404_no_calls(client, base, install):
    f = install(Fakes())
    r = post(client, previous_meeting_id="20990101-01")
    assert r.status_code == 404 and f.events == []
    assert r.json()["detail"]["code"] == "previous_meeting_not_found"


def test_unsafe_previous_meeting_id_404(client, base, install):
    f = install(Fakes())
    assert post(client, previous_meeting_id="../etc").status_code == 404
    assert f.events == []


def test_unanalyzed_previous_meeting_409(client, base, install):
    _make_meeting(base, analyzed=False)
    f = install(Fakes())
    r = post(client)
    assert r.status_code == 409 and f.events == []


def test_reviewer_reject_after_retry_fails_closed_without_tts(client, meeting, install):
    f = install(Fakes(gens=[good(text="초안 문장 REJECTME 입니다.")], verdicts=[REJECT, REJECT]))
    r = post(client)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "briefing_rejected"
    assert "REJECTME" not in r.text and "briefing_text" not in r.text
    assert "tts" not in f.events
    assert f.events == ["gen", "rev", "gen", "rev"]
    assert not (meeting / "briefings").exists() or not list((meeting / "briefings").iterdir())


def test_retry_then_pass_runs_tts_once(client, meeting, install):
    f = install(Fakes(gens=[good(text="첫 초안 입니다."), good()], verdicts=[REJECT, PASS]))
    r = post(client)
    assert r.status_code == 200 and r.json()["briefing_text"] == TEXT
    assert f.events == ["gen", "rev", "gen", "rev", "tts"]


def test_invalid_reviewer_output_fails_closed(client, meeting, install):
    f = install(Fakes(verdicts=["not json", "not json"]))
    r = post(client)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "briefing_review_failed"
    assert "tts" not in f.events


def test_code_check_failure_never_reaches_tts(client, meeting, install):
    bad = good(text="박과장이 6월 30일까지 작업을 완료했습니다.")
    f = install(Fakes(gens=[bad]))
    assert post(client).status_code == 422
    assert "tts" not in f.events and "rev" not in f.events


def test_gemini_not_configured_503(client, meeting):
    r = post(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "gemini_not_configured"


# ---- TTS 입력/오디오
def test_tts_input_is_exactly_approved_text(client, meeting, install):
    f = install(Fakes())
    b = post(client).json()
    assert f.tts_inputs == [b["briefing_text"]] and f.tts_inputs[0] == TEXT


def test_audio_file_created_and_served(client, meeting, install):
    install(Fakes())
    b = post(client).json()
    wav = meeting / "briefings" / f"{b['briefing_id']}.wav"
    assert wav.is_file() and wav.read_bytes()[:4] == b"RIFF"
    r = client.get(b["audio"]["url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav")
    assert r.content == wav.read_bytes()


def test_audio_not_found(client, base):
    r = client.get("/api/briefings/20261110-01_2026-11-12/audio")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "audio_not_found"
    assert client.get("/api/briefings/..%2Fsecret/audio").status_code == 404


# ---- 재사용 / 중복 호출 방지
def test_replay_does_not_call_llm_or_tts_again(client, meeting, install):
    f = install(Fakes())
    first = post(client).json()
    second = post(client)
    assert second.status_code == 200
    s = second.json()
    assert s["reused"] is True and s["briefing_id"] == first["briefing_id"] and s["briefing_text"] == TEXT
    assert f.events == ["gen", "rev", "tts"]
    assert client.get(s["audio"]["url"]).status_code == 200


def test_regenerate_flag_runs_again(client, meeting, install):
    f = install(Fakes())
    post(client)
    r = post(client, regenerate=True)
    assert r.status_code == 200 and r.json()["reused"] is False
    assert f.events.count("gen") == 2 and f.events.count("tts") == 2


def test_double_click_runs_agent_once(client, meeting, install):
    f = install(Fakes(gen_delay=0.3))
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(client))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in out] == [200, 200]
    assert f.events.count("gen") == 1 and f.events.count("tts") == 1


def test_tts_failure_keeps_approved_text_and_retry_skips_llm(client, meeting, install):
    f = install(Fakes(tts_fail=1))
    r = post(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "tts_unavailable"
    r2 = post(client)
    assert r2.status_code == 200 and r2.json()["briefing_text"] == TEXT
    assert f.events == ["gen", "rev", "tts", "tts"]


def test_artifacts_stored_under_mode_data_root(client, meeting, install):
    install(Fakes())
    b = post(client).json()
    assert (meeting / "briefings" / f"{b['briefing_id']}.json").is_file()


def test_existing_api_still_works(client, base):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/meetings").status_code == 200
