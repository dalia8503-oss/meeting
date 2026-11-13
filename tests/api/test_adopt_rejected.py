"""Reviewer가 제외한 후보를 사람이 Action으로 채택한다 (명시적 사람 결정). fake provider/runner만 사용한다."""
import json

import pytest

from meeting_action_agent.api import service

from .test_finalize import _reject_all
from .test_workflow import base, client, fake_deps, store_actions, upload  # noqa: F401


def _deps(m):
    d = fake_deps(m)
    d["reviewer_runner"] = _reject_all  # 모든 Action 후보 제외 → Action 0건
    return d


@pytest.fixture
def analyzed(client, base, monkeypatch):  # noqa: F811
    """화자 확인을 거친 회의. Reviewer가 후보 2건을 모두 제외해 Action 0건."""
    monkeypatch.setattr(service, "_demo_dependencies", _deps)
    mid = upload(client, "2026-11-06")
    client.post(f"/api/meetings/{mid}/transcribe")
    client.put(f"/api/meetings/{mid}/speaker-map", json={"speaker_map": {"speaker_1": "팀장", "speaker_2": "김대리"}})
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200, r.text
    assert r.json()["actions"] == [] and len(r.json()["rejected"]) == 2
    return mid


def _adopt(client, mid, index=0, **body):  # noqa: F811
    return client.post(f"/api/meetings/{mid}/rejected/{index}/adopt", json={"actor_name": "팀장", **body})


def _review_bytes(base, mid):  # noqa: F811
    return (base / "meetings" / f"{mid}.review.json").read_bytes()


def test_rejected_items_carry_index_reasons_and_no_adoption_yet(client, analyzed):  # noqa: F811
    rej = client.get(f"/api/meetings/{analyzed}/result").json()["rejected"]
    assert [r["index"] for r in rej] == [0, 1]
    assert rej[0]["reasons"] and "허위담당자" in rej[0]["reasons"][0]
    assert rej[0]["adopted_action_ids"] == [] and rej[0]["adopted_by"] is None


def test_adopt_creates_flagged_action_without_promoting_status(client, base, analyzed):  # noqa: F811
    review_before = _review_bytes(base, analyzed)
    transcript_before = (base / "transcripts" / "original" / f"{analyzed}.json").read_bytes()
    r = _adopt(client, analyzed)
    assert r.status_code == 200, r.text
    b = r.json()
    [a] = b["new_actions"]
    assert a["assignee"] == "박대리" and a["status"] == "확인 필요"  # 기존 규칙: Reviewer 미통과는 확인 필요
    assert "Reviewer미통과" in a["flags"] and a["approved_by_human"] is None
    stored = next(x for x in store_actions(base) if x["action_id"] == a["action_id"])
    assert stored["history"][0]["actor"] == "Agent"  # 최초 생성 주체는 Agent
    note = stored["flag_notes"]["Reviewer미통과"]["note"]
    assert "채택자: 팀장" in note and "허위담당자" in note  # 누가·왜 제외됐는지 기록
    assert b["rejected"][0]["adopted_action_ids"] == [a["action_id"]] and b["rejected"][0]["adopted_by"] == "팀장"
    assert b["rejected"][1]["adopted_action_ids"] == []
    assert _review_bytes(base, analyzed) == review_before  # Reviewer 판정 기록은 그대로 (REJECT 유지)
    assert json.loads(review_before)["reviews"][0]["verdict"] == "REJECT"
    assert (base / "transcripts" / "original" / f"{analyzed}.json").read_bytes() == transcript_before


def test_adopt_twice_is_rejected(client, analyzed):  # noqa: F811
    assert _adopt(client, analyzed).status_code == 200
    r = _adopt(client, analyzed)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_adopted"


def test_adopt_requires_actor_and_valid_index(client, analyzed):  # noqa: F811
    r = _adopt(client, analyzed, actor_name="  ")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_adoption"
    r = _adopt(client, analyzed, index=9)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "rejected_not_found"
    assert client.get(f"/api/meetings/{analyzed}/result").json()["new_actions"] == []


def test_speaker_label_assignee_needs_human_input_and_is_marked(client, base, analyzed):  # noqa: F811
    path = base / "meetings" / f"{analyzed}.review.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rejected"][0]["assignee"] = "speaker_2"  # LIVE에서 본 사례: 담당자가 화자 라벨
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = _adopt(client, analyzed)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "assignee_required"
    assert not (base / "actions" / "actions.json").exists() or all(a["meeting_id"] != analyzed for a in store_actions(base))
    r = _adopt(client, analyzed, assignees=["박대리"], assigner="팀장")
    assert r.status_code == 200, r.text
    stored = next(x for x in store_actions(base) if x["meeting_id"] == analyzed)
    assert stored["assignee"] == "박대리" and stored["field_source"] == {"assignee": "사람 보강"}


def test_multiple_assignees_create_one_action_each_and_unmentioned_name_stays_unconfirmed(client, analyzed):  # noqa: F811
    r = _adopt(client, analyzed, assignees=["이대리", "박대리"])
    assert r.status_code == 200, r.text
    by = {a["assignee"]: a for a in r.json()["new_actions"]}
    assert len(r.json()["rejected"][0]["adopted_action_ids"]) == 2 and "박대리" in by
    # 발언에 나오지 않은 이름은 기존 담당자 규칙이 그대로 적용되어 '확인 필요'로 남는다 (사람이 입력해도 근거를 만들지 않는다).
    assert "이대리" not in by and by["확인 필요"]["status"] == "확인 필요"


def test_adoption_clears_finalization(client, analyzed):  # noqa: F811
    assert client.post(f"/api/meetings/{analyzed}/finalize").json()["analysis_finalized"] is True
    r = _adopt(client, analyzed)
    assert r.status_code == 200 and not r.json().get("analysis_finalized")


def test_adopt_blocked_when_stale_and_reanalysis_blocked_after_adoption(client, analyzed):  # noqa: F811
    _adopt(client, analyzed)
    r = client.put(f"/api/meetings/{analyzed}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    assert r.json()["stale"] is True
    r = _adopt(client, analyzed, index=1)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "analysis_stale"
    r = client.post(f"/api/meetings/{analyzed}/reanalyze")  # 사람이 채택한 Action은 재분석으로 사라지지 않는다
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reanalysis_blocked"


def test_adopted_action_gets_followed_up_next_meeting(client, base, analyzed, monkeypatch):  # noqa: F811
    """채택한 Action은 2차 회의 이행점검 대상이 된다 (지난 회의 선택 → 이행점검)."""
    assert _adopt(client, analyzed).status_code == 200
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))
    mid2 = upload(client, "2026-11-13")
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [analyzed]})
    assert r.status_code == 200, r.text
    assert r.json()["previous_meeting_ids"] == [analyzed]
