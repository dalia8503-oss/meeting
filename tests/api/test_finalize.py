"""분석 확정(analysis_finalized): 사람이 분석 결과를 확인했다는 표시. Action·Reviewer 판정에는 영향을 주지 않는다.
fake provider/runner만 사용한다 (Gemini 호출 없음)."""
import json

import pytest

from meeting_action_agent.api import service

from .test_workflow import base, client, fake_deps, first, store_actions, upload  # noqa: F401


def _meta(base, mid):  # noqa: F811
    return json.loads((base / "meetings" / f"{mid}.meta.json").read_text(encoding="utf-8"))


def _reject_all(prompt):
    ts = json.JSONDecoder().raw_decode(prompt.split("## ActionCandidate\n", 1)[1])[0]["timestamp"]
    return {"target": {"type": "action", "ref": ts}, "verdict": "REJECT",
            "reasons": [{"code": "허위담당자", "detail": "근거 부족"}], "attempt": 0}


@pytest.fixture
def analyzed(client, monkeypatch, first):  # noqa: F811
    """2차 회의 분석 완료 (Reviewer REJECT 1건 포함). 화자 확인을 거친 회의라 stale 판단 대상이다."""
    mid1, _ = first
    mid2 = upload(client, "2026-11-17")
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [mid1]})
    assert r.status_code == 200, r.text
    return mid1, mid2, r.json()


def test_analysis_result_is_not_finalized_by_default(analyzed):
    _, _, body = analyzed
    assert body["analysis_finalized"] is False and body["analysis_finalized_at"] is None


def test_finalize_saves_flag_and_keeps_actions_and_rejections(client, base, analyzed):  # noqa: F811
    _, mid2, before = analyzed
    actions_before = store_actions(base)
    r = client.post(f"/api/meetings/{mid2}/finalize")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["analysis_finalized"] is True and b["analysis_finalized_at"]
    meta = _meta(base, mid2)
    assert meta["analysis_finalized"] is True and meta["analysis_finalized_at"] == b["analysis_finalized_at"]
    # Reviewer REJECT는 그대로, Action·상태는 한 글자도 바뀌지 않는다.
    assert b["rejected"] == before["rejected"] and len(b["rejected"]) == 1
    assert [a["status"] for a in b["actions"]] == [a["status"] for a in before["actions"]]
    assert store_actions(base) == actions_before
    assert client.get(f"/api/meetings/{mid2}/result").json()["analysis_finalized"] is True


def test_finalize_is_idempotent_and_keeps_first_timestamp(client, analyzed):  # noqa: F811
    _, mid2, _ = analyzed
    t1 = client.post(f"/api/meetings/{mid2}/finalize").json()["analysis_finalized_at"]
    t2 = client.post(f"/api/meetings/{mid2}/finalize").json()["analysis_finalized_at"]
    assert t1 == t2


def test_finalize_with_zero_actions_is_allowed(client, base, monkeypatch, first):  # noqa: F811
    mid2 = upload(client, "2026-11-17")

    def deps(m):
        d = fake_deps(m)
        d["reviewer_runner"] = _reject_all  # 모든 후보를 Reviewer가 제외 → 신규 Action 0건
        return d

    monkeypatch.setattr(service, "_demo_dependencies", deps)
    a = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": []}).json()
    assert a["new_actions"] == [] and a["actions"] == [] and len(a["rejected"]) == 2
    r = client.post(f"/api/meetings/{mid2}/finalize")
    assert r.status_code == 200 and r.json()["analysis_finalized"] is True
    assert r.json()["actions"] == [] and len(r.json()["rejected"]) == 2  # REJECT를 PASS로 바꾸거나 Action을 만들지 않는다
    assert [x["meeting_id"] for x in store_actions(base)] == [first[0]]


def test_finalize_unknown_or_unanalyzed_meeting(client, base):  # noqa: F811
    assert client.post("/api/meetings/20990101-00000000/finalize").status_code == 404
    mid = upload(client, "2026-11-20")
    r = client.post(f"/api/meetings/{mid}/finalize")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_analyzed"
    assert "analysis_finalized" not in _meta(base, mid)


def test_org_info_change_keeps_finalization_and_saves_structured_profile(client, base, analyzed):  # noqa: F811
    _, mid2, _ = analyzed
    client.post(f"/api/meetings/{mid2}/finalize")
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {
        "speaker_2": {"name": "김대리", "department": "2부", "section": "1과"}}})
    assert r.status_code == 200 and r.json()["stale"] is False
    assert _meta(base, mid2)["speaker_map"]["speaker_2"]["display_name"] == "2부 1과 김대리"
    assert _meta(base, mid2)["analysis_finalized"] is True  # 이름이 같으면 확정 유지
    res = client.get(f"/api/meetings/{mid2}/result").json()
    assert res["analysis_finalized"] is True and res["stale"] is False
    assert next(t for t in res["transcript"] if t["speaker"] == "speaker_2")["speaker_display"] == "2부 1과 김대리"


def test_speaker_change_invalidates_finalization_until_reanalysis(client, base, analyzed):  # noqa: F811
    _, mid2, _ = analyzed
    client.post(f"/api/meetings/{mid2}/finalize")
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    assert r.json()["stale"] is True
    assert "analysis_finalized" not in _meta(base, mid2)
    res = client.get(f"/api/meetings/{mid2}/result").json()
    assert res["stale"] is True and res["analysis_finalized"] is False
    blocked = client.post(f"/api/meetings/{mid2}/finalize")
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "analysis_stale"
    # 화자를 원래대로 되돌려도 확정이 자동 복원되지 않는다 (사람이 다시 확정해야 한다).
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert client.get(f"/api/meetings/{mid2}/result").json()["analysis_finalized"] is False


def test_finalize_after_reanalysis(client, base, monkeypatch, first):  # noqa: F811
    # 이전 회의를 쓰지 않은 회의만 재분석할 수 있다 (기존 reanalysis_blocked 정책 유지).
    mid = upload(client, "2026-11-18")
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))
    client.post(f"/api/meetings/{mid}/transcribe")
    client.put(f"/api/meetings/{mid}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert client.post(f"/api/meetings/{mid}/analyze", json={"previous_meeting_ids": []}).status_code == 200
    client.post(f"/api/meetings/{mid}/finalize")
    client.put(f"/api/meetings/{mid}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    r = client.post(f"/api/meetings/{mid}/reanalyze")
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is False and r.json()["analysis_finalized"] is False
    r = client.post(f"/api/meetings/{mid}/finalize")
    assert r.status_code == 200 and r.json()["analysis_finalized"] is True


def test_finalize_does_not_touch_transcript(client, base, analyzed):  # noqa: F811
    _, mid2, _ = analyzed
    path = base / "transcripts" / "original" / f"{mid2}.json"
    before = path.read_bytes()
    client.post(f"/api/meetings/{mid2}/finalize")
    assert path.read_bytes() == before
