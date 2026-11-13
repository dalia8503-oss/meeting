"""화자 1차 추천(AI) → 사람 확인 → 분석 → 분석 후 화자 수정 → stale → 명시적 재분석. fake provider/runner만 사용한다."""
import json

from meeting_action_agent.speakers import build_profile as P

from meeting_action_agent.api import service

from .test_speaker_map import _analyze, _meta, _transcript, two  # noqa: F401
from .test_workflow import DONE_TEXT, base, client, fake_deps, first, store_actions, upload  # noqa: F401


def _suggest(client, mid):  # noqa: F811
    return client.post(f"/api/meetings/{mid}/speaker-suggestions")


def _by(r):
    return {s["speaker_id"]: s for s in r.json()["suggestions"]}


# 1. 명확한 직접 호명 → 올바른 speaker 추천 (근거 포함)
def test_direct_address_suggests_correct_speaker(client, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    r = _suggest(client, mid2)
    assert r.status_code == 200, r.text
    s = _by(r)["speaker_2"]
    assert s["suggested_name"] == "김대리" and s["confidence_level"] == "high" and s["evidence"]


# 2. 근거 부족 → suggested_name null
def test_insufficient_evidence_is_null(client, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    by = _by(_suggest(client, mid2))
    for label in ("speaker_1", "speaker_3"):
        assert by[label]["suggested_name"] is None and by[label]["confidence"] == 0.0
        assert by[label]["evidence"] == [] and by[label]["needs_confirmation"] is True


def test_suggestions_before_transcribe_409(client, two):  # noqa: F811
    _, mid2 = two
    assert _suggest(client, mid2).status_code == 409


def test_hallucinated_runner_output_is_rejected_by_code(client, two, monkeypatch):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    monkeypatch.setattr(service, "_speaker_runner", lambda: lambda p: json.dumps(
        {"speaker_1": {"suggested_name": "이사장", "confidence": 0.99, "evidence": ["추측"]}}))
    assert _by(_suggest(client, mid2))["speaker_1"]["suggested_name"] is None


def test_invalid_runner_output_502_and_nothing_saved(client, base, two, monkeypatch):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    monkeypatch.setattr(service, "_speaker_runner", lambda: lambda p: "JSON 아님")
    r = _suggest(client, mid2)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "speaker_suggestion_invalid"
    assert not (base / "meetings" / f"{mid2}.speaker-suggestions.json").exists()


# 3. AI 추천 저장이 confirmed speaker_map을 바꾸지 않는다
def test_suggestion_does_not_touch_confirmed_map(client, base, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    before = _transcript(base, mid2)
    _suggest(client, mid2)
    assert "speaker_map" not in _meta(base, mid2)
    b = client.get(f"/api/meetings/{mid2}/speakers").json()
    assert b["confirmed"] is False
    assert all(s["confirmed_name"] is None for s in b["speakers"])
    assert b["speakers"][1]["suggestion"]["suggested_name"] == "김대리"  # 추천은 별도 필드로만 보인다
    assert _transcript(base, mid2) == before
    # 이미 확정된 값이 있어도 추천을 다시 만들면 확정값은 그대로다
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    _suggest(client, mid2)
    assert _meta(base, mid2)["speaker_map"] == {"speaker_2": P("박대리")}


# 4, 5, 6. 사용자 확인 → 저장 / 수정 → 수정값 저장 / transcript byte-identical
def test_user_confirms_or_edits_suggestion(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    before = _transcript(base, mid2)
    _suggest(client, mid2)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})  # 그대로 확인
    assert _meta(base, mid2)["speaker_map"] == {"speaker_2": P("김대리")}
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김 대리(수정)"}})  # 수정
    assert _meta(base, mid2)["speaker_map"] == {"speaker_2": P("김 대리(수정)")}
    assert _transcript(base, mid2) == before
    assert _analyze(client, mid2, mid1).status_code == 200
    assert _transcript(base, mid2) == before


# 10. AI 추천만으로는 완료 후보 승격·담당자 확정이 되지 않는다
def test_suggestion_alone_cannot_promote_completion(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    assert _by(_suggest(client, mid2))["speaker_2"]["suggested_name"] == "김대리"
    b = _analyze(client, mid2, mid1).json()  # 사용자가 확인하지 않고 분석
    assert b["followup_results"] == [] and len(b["followup_invalid"]) == 1
    assert next(a for a in store_actions(base) if a["action_id"] == "A-001")["status"] == "신규"
    assert b["speaker_map_used"] == {}


# 11. 사람이 확인한 speaker_map이면 기존 completion rule이 그대로 동작
def test_confirmed_map_still_enables_completion_rule(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    _suggest(client, mid2)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    b = _analyze(client, mid2, mid1).json()
    assert [(f["action_id"], f["to_status"]) for f in b["followup_results"]] == [("A-001", "완료 후보")]
    assert b["speaker_map_used"] == {"speaker_2": "김대리"} and b["stale"] is False
    assert next(a for a in store_actions(base) if a["action_id"] == "A-001")["approved_by_human"] is None


def _analyzed_with_wrong_map(client, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    b = _analyze(client, mid2, mid1).json()
    assert b["followup_results"] == [] and b["stale"] is False
    return mid1, mid2


# 7, 8. 분석 후 speaker 변경 → stale. stale 결과는 최신처럼 쓰이지 않는다
def test_speaker_change_after_analysis_marks_stale_and_blocks_use(client, base, two):  # noqa: F811
    mid1, mid2 = _analyzed_with_wrong_map(client, two)
    before = _transcript(base, mid2)
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert r.status_code == 200 and r.json()["stale"] is True
    assert _transcript(base, mid2) == before
    res = client.get(f"/api/meetings/{mid2}/result").json()
    assert res["stale"] is True and res["speaker_map_used"] == {"speaker_2": "박대리"}  # 기존 결과는 옛 화자 기준
    assert res["followup_results"] == []  # 자동으로 바뀌지 않는다
    assert next(a for a in store_actions(base) if a["action_id"] == "A-001")["status"] == "신규"  # 자동 재분석 없음
    # stale 결과 기반 사람 승인 차단
    new_id = res["new_actions"][0]["action_id"]
    r = client.post(f"/api/actions/{new_id}/review", json={"choice": "확인 필요", "actor_name": "팀장",
                                                           "meeting_id": mid2, "timestamp": "00:00:30"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "analysis_stale"
    # stale 회의를 이전 회의로 쓰는 분석·브리핑 차단
    mid3 = upload(client, "2026-11-24")
    r = client.post(f"/api/meetings/{mid3}/analyze", json={"previous_meeting_ids": [mid2]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "analysis_stale"
    r = client.post("/api/briefings", json={"previous_meeting_id": mid2, "current_date": "2026-11-24"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "analysis_stale"


def test_same_speaker_map_is_not_stale(client, two):  # noqa: F811
    mid1, mid2 = _analyzed_with_wrong_map(client, two)
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    assert r.json()["stale"] is False
    assert client.post(f"/api/meetings/{mid2}/reanalyze").json()["detail"]["code"] == "not_stale"


# 9. 재분석(사용자 명시) → 변경된 speaker_map 반영. 원본 transcript 불변
def test_reanalyze_applies_changed_speaker_map(client, base, two):  # noqa: F811
    mid1, mid2 = _analyzed_with_wrong_map(client, two)
    before = _transcript(base, mid2)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    r = client.post(f"/api/meetings/{mid2}/reanalyze")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["stale"] is False and b["speaker_map_used"] == {"speaker_2": "김대리"}
    assert [(f["action_id"], f["to_status"]) for f in b["followup_results"]] == [("A-001", "완료 후보")]
    assert _transcript(base, mid2) == before
    assert [a["meeting_id"] for a in store_actions(base)].count(mid2) == len(b["new_actions"])  # 중복 생성 없음
    assert client.get(f"/api/meetings/{mid2}/result").json() == b
    assert client.post(f"/api/meetings/{mid2}/reanalyze").status_code == 409  # 더는 stale이 아니다


# 재분석은 fail-closed: 이 회의 판단이 이전 Action에 이미 반영됐으면 거부하고 아무것도 바꾸지 않는다
def test_reanalyze_blocked_when_previous_action_already_updated(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    _analyze(client, mid2, mid1)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    snapshot = (base / "actions" / "actions.json").read_bytes()
    run_before = (base / "meetings" / f"{mid2}.run.json").read_bytes()
    r = client.post(f"/api/meetings/{mid2}/reanalyze")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reanalysis_blocked"
    assert (base / "actions" / "actions.json").read_bytes() == snapshot
    assert (base / "meetings" / f"{mid2}.run.json").read_bytes() == run_before
    assert client.get(f"/api/meetings/{mid2}/result").json()["stale"] is True


def test_reanalyze_failure_restores_previous_results(client, base, two, monkeypatch):  # noqa: F811
    mid1, mid2 = _analyzed_with_wrong_map(client, two)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    snapshot = (base / "actions" / "actions.json").read_bytes()
    run_before = (base / "meetings" / f"{mid2}.run.json").read_bytes()
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m, followup="agent-done"))  # Agent가 완료 제안 → 안전 위반으로 중단
    r = client.post(f"/api/meetings/{mid2}/reanalyze")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "completion_guard_violation"
    assert (base / "actions" / "actions.json").read_bytes() == snapshot
    assert (base / "meetings" / f"{mid2}.run.json").read_bytes() == run_before


def test_reanalyze_requires_analysis(client, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    assert client.post(f"/api/meetings/{mid2}/reanalyze").json()["detail"]["code"] == "not_analyzed"


# 결과에 원본 transcript 줄이 읽기 전용으로 포함된다 (원본 라벨 유지 + 확정 이름 보조)
def test_result_includes_transcript_lines_with_original_labels(client, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    b = _analyze(client, mid2, mid1).json()
    line = next(t for t in b["transcript"] if t["timestamp"] == "00:00:10")
    assert line == {"timestamp": "00:00:10", "speaker": "speaker_2", "speaker_name": "김대리", "speaker_display": "김대리",
                    "speaker_department": None, "speaker_section": None, "text": DONE_TEXT}


def test_delete_meeting_removes_suggestions(client, base, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    _suggest(client, mid2)
    assert client.delete(f"/api/meetings/{mid2}").status_code == 200
    assert not (base / "meetings" / f"{mid2}.speaker-suggestions.json").exists()


# ---- 조직정보(부/과) 프로필
def _put(client, mid, m):
    return client.put(f"/api/meetings/{mid}/speaker-map", json={"speaker_map": m})


def test_org_profile_saved_structured_and_ai_suggestion_stays_separate(client, base, two, monkeypatch):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    monkeypatch.setattr(service, "_speaker_runner", lambda: lambda p: json.dumps(
        {"speaker_2": {"suggested_name": "김대리", "department": "1부", "section": "3과", "confidence": 0.8,
                       "evidence": ["호명 직후 응답"]}}))
    s = _by(_suggest(client, mid2))["speaker_2"]
    assert s["department"] is None and s["section"] is None  # 녹취에 부/과가 없으면 AI도 만들 수 없다
    assert "speaker_map" not in _meta(base, mid2)  # 추천은 확정이 아니다
    r = _put(client, mid2, {"speaker_2": {"name": "김대리", "department": "1부", "section": "3과"}})
    assert r.status_code == 200, r.text
    assert _meta(base, mid2)["speaker_map"]["speaker_2"] == {
        "name": "김대리", "department": "1부", "section": "3과", "display_name": "1부 3과 김대리", "confirmed_by_human": True}
    item = r.json()["speakers"][1]
    assert item["confirmed_name"] == "김대리" and item["confirmed_profile"]["display_name"] == "1부 3과 김대리"


def test_org_info_shown_everywhere_but_identity_and_ids_unchanged(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    before = _transcript(base, mid2)
    _put(client, mid2, {"speaker_1": {"name": "팀장", "department": "1부"},
                        "speaker_2": {"name": "김대리", "department": "1부", "section": "3과"},
                        "speaker_3": {"name": "박대리", "section": "2과"}})
    b = _analyze(client, mid2, mid1).json()
    line = next(t for t in b["transcript"] if t["speaker"] == "speaker_2")
    assert line["speaker"] == "speaker_2" and line["speaker_name"] == "김대리" and line["speaker_display"] == "1부 3과 김대리"
    fr = b["followup_results"][0]["evidence"][0]
    assert fr["speaker"] == "speaker_2" and fr["speaker_display"] == "1부 3과 김대리"
    new = b["new_actions"][0]
    assert new["assignee"] == "박대리" and new["assignee_display"] == "2과 박대리"  # 원문 담당자는 그대로, 표시명만 보강
    assert new["assigner"] == "팀장" and new["assigner_display"] == "1부 팀장"
    assert new["evidence"]["speaker_display"] == "1부 팀장"
    assert b["speaker_map_used"] == {"speaker_1": "팀장", "speaker_2": "김대리", "speaker_3": "박대리"}  # identity=name만
    assert _transcript(base, mid2) == before


def test_org_only_change_after_analysis_is_not_stale_and_updates_display(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    _put(client, mid2, {"speaker_2": {"name": "김대리", "department": "1부", "section": "3과"}})
    first_result = _analyze(client, mid2, mid1).json()
    actions_before = (base / "actions" / "actions.json").read_bytes()
    r = _put(client, mid2, {"speaker_2": {"name": "김대리", "department": "2부", "section": "1과"}})
    assert r.json()["stale"] is False  # 조직정보 수정 = 표시정보 갱신, 재분석 불필요
    res = client.get(f"/api/meetings/{mid2}/result").json()
    assert res["stale"] is False
    assert next(t for t in res["transcript"] if t["speaker"] == "speaker_2")["speaker_display"] == "2부 1과 김대리"
    assert res["followup_results"][0]["evidence"][0]["speaker_display"] == "2부 1과 김대리"  # 저장된 근거도 표시만 갱신
    assert res["followup_results"][0]["to_status"] == first_result["followup_results"][0]["to_status"]  # 판단 불변
    assert (base / "actions" / "actions.json").read_bytes() == actions_before  # Action 데이터 불변


def test_name_change_with_org_is_identity_change_and_stale(client, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    _put(client, mid2, {"speaker_2": {"name": "박대리", "department": "1부"}})
    _analyze(client, mid2, mid1)
    r = _put(client, mid2, {"speaker_2": {"name": "김대리", "department": "1부"}})
    assert r.json()["stale"] is True


def test_org_without_name_rejected(client, base, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    r = _put(client, mid2, {"speaker_2": {"name": " ", "department": "1부"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_speaker_map"
    assert "speaker_map" not in _meta(base, mid2)


def test_ai_org_suggestion_never_affects_completion_rule(client, base, two, monkeypatch):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    monkeypatch.setattr(service, "_speaker_runner", lambda: lambda p: json.dumps(
        {"speaker_2": {"suggested_name": "김대리", "confidence": 0.9, "evidence": ["호명"]}}))
    _suggest(client, mid2)
    b = _analyze(client, mid2, mid1).json()
    assert b["followup_results"] == [] and b["speaker_map_used"] == {}
