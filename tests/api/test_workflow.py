"""FastAPI → Multi-Agent Orchestrator → Human Review 연결. fake/fixture runner만 사용한다 (Gemini·Claude 호출 없음).

1차 회의는 DEMO 고정 시나리오(A-001: 김대리 '도면 수정해서 공유')로, 2차 회의는 같은 서비스 경로에
fake STT·Analyst·Follow-up·Reviewer를 주입해(`service._demo_dependencies` 교체) 실행한다.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_action_agent.api import demo, service
from meeting_action_agent.api.app import create_app
from meeting_action_agent.stt.gemini_stt import GeminiSttProvider

WAV = Path(__file__).resolve().parents[1] / "fixtures" / "stt" / "tts_meeting_001.wav"

DONE_TEXT = "수정 완료해서 어제 공유했습니다."
NEW_TEXT = "좋습니다. 그러면 이번에는 박 대리님이 다음주 화요일까지 검토 의견 정리해서 공유해 주세요."
PROPOSAL = "다음에는 품질 문제도 한번 보면 좋겠습니다."
SEGMENTS = [
    ("speaker_1", "00:00", "김 대리, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?"),
    ("speaker_2", "00:10", DONE_TEXT),
    ("speaker_1", "00:30", NEW_TEXT),
    ("speaker_3", "00:40", "네, 알겠습니다."),
    ("speaker_1", "01:00", PROPOSAL),
    ("speaker_1", "01:10", "도면 검토는 끝났나요?"),
]
STT2 = json.dumps({"segments": [{"speaker": s, "timestamp": t, "text": x} for s, t, x in SEGMENTS]}, ensure_ascii=False)


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("MAA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("MAA_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "runtime" / "demo"


@pytest.fixture
def client(base):
    return TestClient(create_app())


def upload(client, date):
    r = client.post("/api/meetings", data={"title": "주간", "meeting_date": date},
                    files={"audio_file": ("m.wav", WAV.read_bytes(), "audio/wav")})
    assert r.status_code == 201
    return r.json()["meeting_id"]


def _after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def _utt(transcript, text):
    return next(u for u in transcript if u["text"] == text)


def fake_deps(mid, *, candidates="ok", followup="done", verdict="PASS"):
    """2차 회의용 fake 의존성. 판정은 시나리오 고정값이며 외부 호출은 없다."""
    def analyst(prompt):
        if candidates == "invalid":
            return "이건 JSON이 아니다"
        cands = [{"instruction_type": "지시·요청", "candidate": {
            "meeting_id": mid, "assigner": "팀장", "assignee": "박대리", "task": "검토 의견 정리해서 공유",
            "due_date": "다음주 화요일까지", "due_date_normalized": None, "evidence": NEW_TEXT,
            "timestamp": "00:00:30", "status": "신규", "flags": []}}]
        cands.append({"instruction_type": "지시·요청", "candidate": {
            "meeting_id": mid, "assigner": "팀장", "assignee": "팀장", "task": "품질 문제 보기",
            "due_date": "미정", "due_date_normalized": None, "evidence": PROPOSAL,
            "timestamp": "00:01:00", "status": "신규", "flags": []}})
        return json.dumps({"meeting_id": mid, "key_discussions": [{"text": "도면 점검", "evidence_timestamps": ["00:00:10"]}],
                           "decisions": [], "open_issues": [], "needs_confirmation": [],
                           "action_candidates": cands}, ensure_ascii=False)

    def cand_reviewer(prompt):
        ts = _after(prompt, "## ActionCandidate\n")["timestamp"]
        bad = ts == "00:01:00"  # 제안 발언은 Action이 될 수 없다
        return {"target": {"type": "action", "ref": ts}, "verdict": "REJECT" if bad else "PASS",
                "reasons": [{"code": "허위담당자", "detail": "제안"}] if bad else [], "attempt": 0}

    def followup_runner(prompt):
        p = _after(prompt, "## INPUT\n")
        u = _utt(p["transcript"], DONE_TEXT)
        status = "완료" if followup == "agent-done" else "완료 후보"
        return json.dumps({"meeting_id": mid, "action_id": p["action"]["action_id"], "proposed_status": status,
                           "confidence": "상", "flags_to_add": [], "reason": "완료형 발언",
                           "evidence": [{"meeting_id": mid, "timestamp": u["timestamp"], "speaker": u["speaker"],
                                         "text": u["text"]}]}, ensure_ascii=False)

    def followup_reviewer(prompt):
        ref = _after(prompt, "## INPUT\n")["followup_result"]["action_id"]
        return {"target": {"type": "followup", "ref": ref}, "verdict": verdict,
                "reasons": [] if verdict == "PASS" else [{"code": "판정비도출", "detail": "근거 불충분"}], "attempt": 0}

    return dict(provider=GeminiSttProvider(call=lambda *a: STT2), analyst_runner=analyst, reviewer_runner=cand_reviewer,
                speaker_map=demo.DEMO_SPEAKER_MAP, followup_runner=followup_runner,
                followup_reviewer_runner=followup_reviewer)


@pytest.fixture
def first(client):
    mid = upload(client, "2026-11-10")
    r = client.post(f"/api/meetings/{mid}/analyze")  # 기존 DEMO 경로: body 없음
    assert r.status_code == 200, r.text
    return mid, r.json()


def second(client, monkeypatch, first_mid, ids="auto", **kw):
    mid = upload(client, "2026-11-17")
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m, **kw))
    body = {"previous_meeting_ids": [first_mid] if ids == "auto" else ids}
    r = client.post(f"/api/meetings/{mid}/analyze", json=body) if ids is not None else \
        client.post(f"/api/meetings/{mid}/analyze")
    return mid, r


def store_actions(base):
    return json.loads((base / "actions" / "actions.json").read_text(encoding="utf-8"))


# 1. health regression
def test_health_still_works(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["mode"] == "demo"


# 2. 1차 회의: 기존 응답 필드 유지 + Orchestrator가 순차로 처리
def test_first_meeting_keeps_legacy_fields_and_runs_sequentially(first):
    mid, b = first
    assert (b["meeting_id"], b["status"], b["mode"], b["utterance_count"]) == (mid, "analyzed", "demo", 8)
    assert [a["action_id"] for a in b["actions"]] == ["A-001"] and b["rejected"] == []
    assert b["execution"]["mode"] == "sequential" and b["execution"]["overlapped"] is False
    assert [a["agent"] for a in b["execution"]["agents"]] == ["meeting-analyst"]
    assert b["followup_results"] == [] and b["previous_meeting_ids"] == []
    assert [a["action_id"] for a in b["new_actions"]] == ["A-001"]
    assert b["meeting_analysis"]["summary"] == b["summary"] and b["meeting_analysis"]["open_issues"]
    assert b["actions"][0]["history"][0]["to_status"] == "신규" and b["actions"][0]["approved_by_human"] is None


# 3. previous_meeting_ids 없음 → 신규 회의 분석만 (이전 Action 불변, Follow-up Agent 미실행)
@pytest.mark.parametrize("ids", [None, []])
def test_second_meeting_without_previous_ids_only_analyzes_new(client, base, first, monkeypatch, ids):
    mid1, _ = first
    before = store_actions(base)[0]
    mid2, r = second(client, monkeypatch, mid1, ids=ids)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["execution"]["mode"] == "sequential" and [a["agent"] for a in b["execution"]["agents"]] == ["meeting-analyst"]
    assert b["followup_results"] == []
    assert store_actions(base)[0] == before
    assert [a["action_id"] for a in b["new_actions"]] == ["A-002"]


# 4~11. previous_meeting_ids 있음 → Multi-Agent 병렬, Follow-up 결과·신규 Action·proposal 제외
def test_second_meeting_with_previous_ids_runs_multi_agent(client, base, first, monkeypatch):
    mid1, _ = first
    mid2, r = second(client, monkeypatch, mid1)
    assert r.status_code == 200, r.text
    b = r.json()
    # 5, 6. Multi-Agent mode / execution metadata (thread 정보는 노출하지 않는다)
    ex = b["execution"]
    assert ex["mode"] == "parallel" and ex["overlapped"] in (True, False)
    assert {a["agent"] for a in ex["agents"]} == {"meeting-analyst", "followup-agent"}
    assert all(set(a) == {"agent", "status", "started_at", "finished_at", "reviewer", "error"} for a in ex["agents"])
    assert all(a["status"] == "ok" and a["finished_at"] >= a["started_at"] >= 0 for a in ex["agents"])
    assert ex["reviewer"] == {"PASS": 2, "REJECT": 1}  # 신규 후보 2건(PASS·REJECT) + Follow-up 1건(PASS)
    assert b["previous_meeting_ids"] == [mid1]
    # 8, 9. Follow-up 결과: A-001 완료 후보, 근거(원문·화자·타임스탬프)
    fr = b["followup_results"]
    assert [(f["action_id"], f["outcome"], f["from_status"], f["to_status"], f["reviewer_verdict"]) for f in fr] == \
        [("A-001", "applied", "신규", "완료 후보", "PASS")]
    assert fr[0]["evidence"] == [{"timestamp": "00:00:10", "speaker": "김대리", "speaker_name": None, "text": DONE_TEXT}]
    # 7, 10, 11. 신규 Action: A-002만. proposal(품질 문제)은 Action이 아니다
    assert [(a["action_id"], a["assignee"], a["status"]) for a in b["new_actions"]] == [("A-002", "박대리", "신규")]
    assert len(b["rejected"]) == 1 and b["rejected"][0]["evidence"] == PROPOSAL
    # actions = 이번 회의 Action + 이행점검 대상 이전 Action의 최종 상태
    by_id = {a["action_id"]: a for a in b["actions"]}
    assert set(by_id) == {"A-001", "A-002"} and by_id["A-001"]["status"] == "완료 후보"
    assert by_id["A-001"]["meeting_id"] == mid1 and by_id["A-001"]["approved_by_human"] is None
    assert all("품질" not in a["task"] for a in store_actions(base))
    # get_result는 같은 응답을 재현한다
    assert client.get(f"/api/meetings/{mid2}/result").json() == b


def test_previous_meeting_validation(client, first):
    mid1, _ = first
    mid2 = upload(client, "2026-11-17")
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": ["20200101-deadbeef"]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "previous_meeting_not_found"
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [mid2]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_previous_meeting"
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": ["a..b"]})
    assert r.status_code == 404


# 13~. 격리 가능한 오류는 응답에 반영된다 (Follow-up 반영은 유지)
def test_new_actions_error_is_isolated_and_reported(client, base, first, monkeypatch):
    mid1, _ = first
    mid2, r = second(client, monkeypatch, mid1, candidates="invalid")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["new_actions_error"] and "AnalysisValidationError" in b["new_actions_error"]
    assert b["meeting_analysis"] is None and b["new_actions"] == []
    assert [(f["action_id"], f["to_status"]) for f in b["followup_results"]] == [("A-001", "완료 후보")]
    assert [a["action_id"] for a in b["actions"]] == ["A-001"] and b["actions"][0]["status"] == "완료 후보"
    assert [x["action_id"] for x in store_actions(base)] == ["A-001"]
    assert b["execution"]["agents"][0]["status"] == "error"
    assert client.get(f"/api/meetings/{mid2}/result").json() == b
    assert client.post(f"/api/meetings/{mid2}/analyze").status_code == 409  # 재분석으로 덮어쓰지 않는다


def test_followup_reviewer_reject_is_reported_per_action(client, base, first, monkeypatch):
    mid1, _ = first
    mid2, r = second(client, monkeypatch, mid1, verdict="REJECT")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [(f["action_id"], f["outcome"], f["reviewer_verdict"]) for f in b["followup_results"]] == \
        [("A-001", "rejected", "REJECT")]
    assert store_actions(base)[0]["status"] == "신규"  # REJECT는 Action을 바꾸지 않는다
    assert [a["action_id"] for a in b["new_actions"]] == ["A-002"]  # 신규 경로는 독립


# 20, 안전 위반 fail-closed
def test_agent_proposing_done_is_409_and_writes_nothing(client, base, first, monkeypatch):
    mid1, _ = first
    before = store_actions(base)
    mid2, r = second(client, monkeypatch, mid1, followup="agent-done")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "completion_guard_violation"
    assert store_actions(base) == before
    assert not (base / "meetings" / f"{mid2}.run.json").exists()
    assert not (base / "meetings" / f"{mid2}.analysis.json").exists()


# snapshot 충돌도 성공으로 처리하지 않는다
def test_followup_snapshot_conflict_is_409_and_writes_nothing(client, base, first, monkeypatch):
    mid1, _ = first
    mid2 = upload(client, "2026-11-17")
    (base / "meetings" / f"{mid2}.followups.json").write_text("[]", encoding="utf-8")
    before = store_actions(base)
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [mid1]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "followup_snapshot_conflict"
    assert store_actions(base) == before and not (base / "meetings" / f"{mid2}.run.json").exists()


# ---------------------------------------------------------------- Human Review
@pytest.fixture
def reviewed(client, base, first, monkeypatch):
    mid1, _ = first
    mid2, r = second(client, monkeypatch, mid1)
    assert r.status_code == 200
    return mid2


def review(client, action_id, **body):
    return client.post(f"/api/actions/{action_id}/review", json=body)


# 12, 13, 14. 완료 성공 → 최신 Action 전체 반환
def test_review_done_returns_full_action_with_approval(client, base, reviewed):
    r = review(client, "A-001", choice="완료", actor_name="팀장", meeting_id=reviewed, timestamp="00:00:10")
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["action_id"], a["status"]) == ("A-001", "완료")
    ap = a["approved_by_human"]
    assert ap["approver"] == "팀장" and ap["evidence"] == {"meeting_id": reviewed, "timestamp": "00:00:10"}
    last = a["history"][-1]
    assert (last["actor"], last["from_status"], last["to_status"], last["actor_name"]) == ("사람", "완료 후보", "완료", "팀장")
    assert store_actions(base)[0]["status"] == "완료"
    assert a["task"] and a["evidence"]["speaker"] == "팀장"  # 전체 Action 필드


# 15. 빈 승인자
@pytest.mark.parametrize("name", ["", "   "])
def test_empty_actor_name_rejected(client, base, reviewed, name):
    r = review(client, "A-001", choice="완료", actor_name=name, meeting_id=reviewed, timestamp="00:00:10")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_review_request"
    assert store_actions(base)[0]["status"] == "완료 후보"


def test_done_requires_approval_evidence(client, base, reviewed):
    r = review(client, "A-001", choice="완료", actor_name="팀장")
    assert r.status_code == 422
    assert store_actions(base)[0]["status"] == "완료 후보"


# 16. 완료 후보가 아닌 Action 완료 거부
def test_done_on_non_candidate_is_409(client, base, reviewed):
    r = review(client, "A-002", choice="완료", actor_name="팀장", meeting_id=reviewed, timestamp="00:00:30")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invalid_transition"
    assert next(a for a in store_actions(base) if a["action_id"] == "A-002")["status"] == "신규"


# 17, 18. 계속 진행 / 확인 필요
def test_continue_and_needs_check(client, base, reviewed):
    r = review(client, "A-001", choice="계속 진행", actor_name="팀장")
    assert r.status_code == 200 and r.json()["status"] == "진행중" and r.json()["approved_by_human"] is None
    assert r.json()["history"][-1]["actor"] == "사람"
    r = review(client, "A-001", choice="확인 필요", actor_name="팀장")
    assert r.status_code == 409  # 진행중 → 확인 필요는 사람 전이표에 없다 (도메인 규칙 그대로)


def test_needs_check_from_done_candidate(client, reviewed):
    r = review(client, "A-001", choice="확인 필요", actor_name="팀장")
    assert r.status_code == 200 and r.json()["status"] == "확인 필요"


# 19. 존재하지 않는 Action, invalid choice
def test_unknown_action_is_404(client, reviewed):
    r = review(client, "A-999", choice="계속 진행", actor_name="팀장")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "action_not_found"


def test_invalid_choice_is_422(client, base, reviewed):
    r = review(client, "A-001", choice="삭제", actor_name="팀장")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_review_request"
    assert store_actions(base)[0]["status"] == "완료 후보"


# CompletionGuardError → 409 (fail-closed)
def test_completion_guard_error_maps_to_409(client, reviewed, monkeypatch):
    from meeting_action_agent.state import CompletionGuardError

    def boom(*a, **k):
        raise CompletionGuardError("x")
    monkeypatch.setattr(service, "review_action", boom)
    r = review(client, "A-001", choice="완료", actor_name="팀장", meeting_id=reviewed, timestamp="00:00:10")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "completion_guard_violation"


# 완료 철회 (T-10-4 domain을 얇게 노출)
def test_withdraw_completion(client, base, reviewed):
    review(client, "A-001", choice="완료", actor_name="팀장", meeting_id=reviewed, timestamp="00:00:10")
    r = client.post("/api/actions/A-001/withdraw-completion", json={"actor_name": "팀장"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "진행중" and a["approved_by_human"] is not None  # 승인값 보존
    assert client.post("/api/actions/A-001/withdraw-completion", json={"actor_name": "팀장"}).status_code == 409
    assert client.post("/api/actions/A-001/withdraw-completion", json={"actor_name": " "}).status_code in (409, 422)
    assert client.post("/api/actions/A-999/withdraw-completion", json={"actor_name": "팀장"}).status_code == 404


# 21, 22. DEMO / LIVE 구조 regression (기존 의존성 dict에 Follow-up runner가 없어도 동작)
def test_live_deps_without_followup_runners_fall_back_to_legacy_path(client, base, monkeypatch):
    monkeypatch.setenv("MAA_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    mid = upload(client, "2026-11-10")
    sample = (Path(demo.__file__).parent / "demo_data" / "stt_response.json").read_text(encoding="utf-8")
    monkeypatch.setattr(service, "_live_dependencies", lambda: dict(
        provider=GeminiSttProvider(call=lambda *a: sample), analyst_runner=demo.make_demo_analyst(mid),
        reviewer_runner=demo.demo_reviewer, speaker_map=None))
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200 and r.json()["mode"] == "live" and r.json()["execution"] is None
    assert [a["action_id"] for a in r.json()["actions"]] == ["A-001"]


def test_live_dependencies_include_followup_runners(monkeypatch):
    """LIVE 의존성 구조: Orchestrator가 쓰는 Follow-up runner가 포함된다 (호출은 하지 않는다)."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    deps = service._live_dependencies()
    assert deps["followup_runner"] is deps["followup_reviewer_runner"] is deps["reviewer_runner"]
    assert set(service._demo_dependencies("20261110-aaaaaaaa")) == set(deps)


def test_demo_second_meeting_uses_demo_followup_agent(client, base, first):
    """실제 DEMO 의존성만으로 2차 회의: 고정 녹취는 이전 Action을 언급하지 않아 Action 불변(skipped)."""
    mid1, _ = first
    mid2 = upload(client, "2026-11-17")
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [mid1]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["execution"]["mode"] == "parallel"
    assert [(f["action_id"], f["outcome"]) for f in b["followup_results"]] == [("A-001", "skipped")]
    assert store_actions(base)[0]["status"] == "신규"
