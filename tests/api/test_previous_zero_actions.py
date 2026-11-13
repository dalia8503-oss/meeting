"""Action 0건으로 분석된 회의도 이전 회의로 선택할 수 있다 (이행점검 대상이 없을 뿐 오류가 아니다)."""
from meeting_action_agent.api import service

from .test_finalize import _reject_all
from .test_workflow import base, client, fake_deps, upload  # noqa: F401


def test_previous_meeting_with_zero_actions_is_selectable(client, monkeypatch):  # noqa: F811
    def deps(m):
        d = fake_deps(m)
        d["reviewer_runner"] = _reject_all  # 모든 Action 후보 제외 → Action 0건
        return d

    monkeypatch.setattr(service, "_demo_dependencies", deps)
    mid1 = upload(client, "2026-11-06")
    assert client.post(f"/api/meetings/{mid1}/analyze").status_code == 200
    listed = next(m for m in client.get("/api/meetings").json()["meetings"] if m["meeting_id"] == mid1)
    assert listed["analysis_status"] == "analyzed" and listed["action_count"] == 0
    mid2 = upload(client, "2026-11-13")
    r = client.post(f"/api/meetings/{mid2}/analyze", json={"previous_meeting_ids": [mid1]})
    assert r.status_code == 200, r.text
    assert r.json()["previous_meeting_ids"] == [mid1] and r.json()["followup_results"] == []
