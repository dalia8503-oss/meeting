"""AI 재분석(force): 화자 변경(stale)이 없어도 같은 녹취로 분석을 다시 실행한다. fake runner만 사용한다."""
import json

import pytest

from meeting_action_agent.api import service

from .test_adopt_rejected import _adopt, _deps, _review_bytes, analyzed  # noqa: F401
from .test_workflow import base, client, fake_deps, store_actions  # noqa: F401


def _transcript(base, mid):  # noqa: F811
    return (base / "transcripts" / "original" / f"{mid}.json").read_bytes()


def _force(client, mid):  # noqa: F811
    return client.post(f"/api/meetings/{mid}/reanalyze", json={"force": True})


def test_reanalyze_without_force_still_requires_stale(client, analyzed):  # noqa: F811
    r = client.post(f"/api/meetings/{analyzed}/reanalyze")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_stale"
    r = client.post(f"/api/meetings/{analyzed}/reanalyze", json={"force": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_stale"


def test_force_reanalyze_replaces_results_keeps_transcript_and_clears_finalization(client, base, analyzed, monkeypatch):  # noqa: F811
    assert client.get(f"/api/meetings/{analyzed}/result").json()["actions"] == []  # Reviewer가 모두 제외한 1차 결과
    client.post(f"/api/meetings/{analyzed}/finalize")
    transcript_before = _transcript(base, analyzed)
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))  # 이번에는 Reviewer가 통과시킨다
    r = _force(client, analyzed)
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["actions"]) >= 1 and len(b["rejected"]) < 2  # 새 분석 결과로 교체됨
    assert not b.get("analysis_finalized")  # 새 결과는 사람이 다시 확정해야 한다
    assert b["stale"] is False
    assert _transcript(base, analyzed) == transcript_before  # 원본 녹취록 불변 (STT 재실행 없음)
    assert client.get(f"/api/meetings/{analyzed}/result").json() == b
    ids = [a["action_id"] for a in store_actions(base) if a["meeting_id"] == analyzed]
    assert len(ids) == len(set(ids)) == len(b["new_actions"])  # Action 중복 없음


def test_force_reanalyze_failure_restores_previous_results(client, base, analyzed, monkeypatch):  # noqa: F811
    from meeting_action_agent.reviewer import ReviewOutputError
    review_before = _review_bytes(base, analyzed)
    run_before = (base / "meetings" / f"{analyzed}.run.json").read_bytes()
    analysis_before = (base / "meetings" / f"{analyzed}.analysis.json").read_bytes()
    actions_path = base / "actions" / "actions.json"
    actions_before = actions_path.read_bytes() if actions_path.is_file() else None

    def boom(*a, **k):
        raise ReviewOutputError("검증 실패")

    monkeypatch.setattr(service, "run_audio_pipeline", boom)
    r = _force(client, analyzed)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "review_failed"
    assert _review_bytes(base, analyzed) == review_before  # 실패하면 기존 결과를 그대로 복원한다
    assert (base / "meetings" / f"{analyzed}.run.json").read_bytes() == run_before
    assert (base / "meetings" / f"{analyzed}.analysis.json").read_bytes() == analysis_before
    assert (actions_path.read_bytes() if actions_path.is_file() else None) == actions_before
    assert client.get(f"/api/meetings/{analyzed}/result").status_code == 200


def test_force_reanalyze_blocked_after_human_adoption(client, base, analyzed):  # noqa: F811
    assert _adopt(client, analyzed).status_code == 200
    review_before = _review_bytes(base, analyzed)
    r = _force(client, analyzed)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reanalysis_blocked"  # 사람이 채택한 Action은 지키지 않고 덮지 않는다
    assert _review_bytes(base, analyzed) == review_before
    assert client.get(f"/api/meetings/{analyzed}/result").json()["new_actions"]


def test_force_reanalyze_requires_prior_analysis(client, base, monkeypatch):  # noqa: F811
    from .test_workflow import upload
    mid = upload(client, "2026-11-09")
    r = _force(client, mid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_analyzed"
