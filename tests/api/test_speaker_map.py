"""T-LIVE-SPK API/E2E: STT 준비 → 사용자 화자 확인 → 분석. fake provider/runner만 사용한다 (Gemini 호출 없음).

LIVE 실패 재현 조건: STT는 speaker_N 라벨만 주고, 완료 발언("수정 완료해서 어제 공유했습니다.")에는 담당자 이름이 없다.
"""
import json

import pytest

from meeting_action_agent.api import service

from .test_workflow import DONE_TEXT, STT2, base, client, fake_deps, first, store_actions, upload  # noqa: F401


@pytest.fixture
def two(client, monkeypatch, first):  # noqa: F811
    """2차 회의: fake LIVE-유사 STT(speaker_N). 아직 transcribe 전."""
    mid1, _ = first
    mid2 = upload(client, "2026-11-17")
    monkeypatch.setattr(service, "_demo_dependencies", lambda m: fake_deps(m))
    return mid1, mid2


def _transcript(base, mid):  # noqa: F811
    return (base / "transcripts" / "original" / f"{mid}.json").read_bytes()


def _meta(base, mid):  # noqa: F811
    return json.loads((base / "meetings" / f"{mid}.meta.json").read_text(encoding="utf-8"))


def _analyze(client, mid, prev):  # noqa: F811
    return client.post(f"/api/meetings/{mid}/analyze", json={"previous_meeting_ids": [prev]})


# 1, 2, 15. STT 준비 단계: 화자 목록 + 실제 대표 발언, 조회 가능
def test_transcribe_returns_speakers_with_real_samples(client, two):
    _, mid2 = two
    r = client.post(f"/api/meetings/{mid2}/transcribe")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [s["speaker"] for s in b["speakers"]] == ["speaker_1", "speaker_2", "speaker_3"]
    assert b["confirmed"] is False and all(s["confirmed_name"] is None for s in b["speakers"])
    s2 = b["speakers"][1]
    assert s2["utterance_count"] == 1 and s2["samples"] == [{"timestamp": "00:00:10", "text": DONE_TEXT}]
    assert client.get(f"/api/meetings/{mid2}/speakers").json() == b  # 15. 조회
    assert client.post(f"/api/meetings/{mid2}/transcribe").json() == b  # 멱등


def test_speakers_before_transcribe_is_409(client, two):
    _, mid2 = two
    r = client.get(f"/api/meetings/{mid2}/speakers")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "transcript_not_ready"
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert r.status_code == 409


def test_unknown_meeting_404(client):
    assert client.post("/api/meetings/20200101-deadbeef/transcribe").status_code == 404
    assert client.get("/api/meetings/20200101-deadbeef/speakers").status_code == 404


# 3, 15. 유효한 speaker_map 저장 (meeting metadata, 계약 형태) + 조회에 반영
def test_put_speaker_map_saves_in_meeting_metadata(client, base, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    r = client.put(f"/api/meetings/{mid2}/speaker-map",
                   json={"speaker_map": {"speaker_1": "팀장", "speaker_2": "김대리", "speaker_3": None}})
    assert r.status_code == 200, r.text
    assert _meta(base, mid2)["speaker_map"] == {"speaker_1": {"name": "팀장"}, "speaker_2": {"name": "김대리"}}
    b = client.get(f"/api/meetings/{mid2}/speakers").json()
    assert b["confirmed"] is True
    assert [s["confirmed_name"] for s in b["speakers"]] == ["팀장", "김대리", None]  # 미확인은 null


# 4, 5. 잘못된 mapping 거부 (저장 안 됨)
@pytest.mark.parametrize("bad", [{"speaker_99": "팀장"}, {"speaker_2": ""}, {"speaker_2": "   "}])
def test_invalid_speaker_map_rejected(client, base, two, bad):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": bad})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_speaker_map"
    assert "speaker_map" not in _meta(base, mid2)


# 6. 원본 transcript 불변 (저장·분석 전후 byte 동일, speaker_N 유지)
def test_original_transcript_is_immutable(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    before = _transcript(base, mid2)
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_1": "팀장", "speaker_2": "김대리"}})
    assert _transcript(base, mid2) == before
    assert _analyze(client, mid2, mid1).status_code == 200
    assert _transcript(base, mid2) == before
    assert {u["speaker"] for u in json.loads(before)} == {"speaker_1", "speaker_2", "speaker_3"}


# 9. mapping 없음 + 이름 없는 완료 발언 → 완료 후보 금지 (LIVE 실패 재현 = 올바른 fail-closed)
def test_without_speaker_map_completion_not_proposed(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    r = _analyze(client, mid2, mid1)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["followup_results"] == [] and len(b["followup_invalid"]) == 1
    a1 = next(a for a in store_actions(base) if a["action_id"] == "A-001")
    assert a1["status"] == "신규"


# 12. 확인 단계를 건너뛰어도(미확인) 동일하게 fail-closed. 일부 화자만 확인해도 speaker_2가 미확인이면 금지
def test_partially_confirmed_other_speaker_not_enough(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_1": "팀장", "speaker_2": None}})
    b = _analyze(client, mid2, mid1).json()
    assert b["followup_results"] == [] and len(b["followup_invalid"]) == 1


# 10, 14, 22. 올바른 mapping → A-001 완료 후보. Evidence는 speaker_N 원본 + 확인 이름 보조
def test_correct_speaker_map_enables_completion_candidate(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_1": "팀장", "speaker_2": "김대리"}})
    r = _analyze(client, mid2, mid1)
    assert r.status_code == 200, r.text
    b = r.json()
    fr = b["followup_results"]
    assert [(f["action_id"], f["outcome"], f["to_status"], f["reviewer_verdict"]) for f in fr] == \
        [("A-001", "applied", "완료 후보", "PASS")]
    assert fr[0]["evidence"] == [{"timestamp": "00:00:10", "speaker": "speaker_2", "speaker_name": "김대리",
                                  "text": DONE_TEXT}]
    a1 = next(a for a in store_actions(base) if a["action_id"] == "A-001")
    assert a1["status"] == "완료 후보" and a1["approved_by_human"] is None  # Agent는 후보까지만. 완료는 Human Review
    # 신규 Action 근거에도 원본 라벨 + 확인 이름
    new = b["new_actions"][0]["evidence"]
    assert new["speaker"] == "speaker_1" and new["speaker_name"] == "팀장"
    assert client.get(f"/api/meetings/{mid2}/result").json() == b


# 11. 다른 사람으로 mapping → 완료 후보 금지
def test_mapping_to_other_person_blocks_completion(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    b = _analyze(client, mid2, mid1).json()
    assert b["followup_results"] == [] and len(b["followup_invalid"]) == 1
    assert next(a for a in store_actions(base) if a["action_id"] == "A-001")["status"] == "신규"


# 23. Human Review regression: 완료 후보 → 사람 승인으로만 완료
def test_human_review_after_confirmed_completion_candidate(client, base, two):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert _analyze(client, mid2, mid1).status_code == 200
    r = client.post("/api/actions/A-001/review", json={"choice": "완료", "actor_name": "팀장",
                                                       "meeting_id": mid2, "timestamp": "00:00:10"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "완료" and r.json()["approved_by_human"]["approver"] == "팀장"


# 7, 8, 20, 21. meeting-scoped: 1차 회의 매핑이 다른 회의로 복사·재사용되지 않는다
def test_speaker_map_is_meeting_scoped_and_never_reused(client, base, two, monkeypatch):  # noqa: F811
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_1": "팀장", "speaker_2": "김대리"}})
    mid3 = upload(client, "2026-11-24")
    r = client.post(f"/api/meetings/{mid3}/transcribe")
    assert r.status_code == 200
    assert all(s["confirmed_name"] is None for s in r.json()["speakers"]) and r.json()["confirmed"] is False
    assert "speaker_map" not in _meta(base, mid3)
    assert _meta(base, mid2)["speaker_map"]["speaker_2"] == {"name": "김대리"}  # 2차 회의 매핑은 그대로
    # 3차 회의를 확인 없이 분석하면 speaker_2는 미확인 → 완료 후보 금지
    a = next(x for x in store_actions(base) if x["action_id"] == "A-001")
    assert a["status"] == "신규"
    b = _analyze(client, mid3, mid1).json()
    assert b["followup_results"] == [] and len(b["followup_invalid"]) == 1


# 확인은 분석 전에만, 분석 후에는 변경 불가 (이미 사용된 근거를 바꾸지 않는다)
def test_speaker_map_locked_after_analysis(client, two):
    mid1, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    assert _analyze(client, mid2, mid1).status_code == 200
    r = client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_analyzed"


# 다시 저장하면 전체 교체 (사용자가 정정 가능)
def test_speaker_map_replace_before_analysis(client, base, two):  # noqa: F811
    _, mid2 = two
    client.post(f"/api/meetings/{mid2}/transcribe")
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "박대리"}})
    client.put(f"/api/meetings/{mid2}/speaker-map", json={"speaker_map": {"speaker_2": "김대리"}})
    assert _meta(base, mid2)["speaker_map"] == {"speaker_2": {"name": "김대리"}}


# 17. 기존 DEMO regression: transcribe 없이 analyze (기존 호환 경로)는 그대로
def test_legacy_analyze_without_transcribe_unchanged(client, first):  # noqa: F811
    _, b = first
    assert [a["action_id"] for a in b["actions"]] == ["A-001"] and b["actions"][0]["evidence"]["speaker"] == "팀장"


# 17-b. DEMO: transcribe 후 확인 없이 분석해도 DEMO가 깨지지 않는다 (1차 회의, 이전 회의 없음)
def test_demo_flow_with_transcribe_step(client):
    mid = upload(client, "2026-11-10")
    assert client.post(f"/api/meetings/{mid}/transcribe").status_code == 200
    r = client.post(f"/api/meetings/{mid}/analyze")
    assert r.status_code == 200, r.text
    assert r.json()["execution"]["mode"] == "sequential" and r.json()["utterance_count"] == 8
