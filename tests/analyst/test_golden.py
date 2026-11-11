"""T-05-7: 최소 정답표(5건). 실제 LLM 정확도 테스트가 아니라 Meeting Analyst가 따라야 할 기대 계약을 고정한다.

- GOLDEN: 발언 → (기대 유형, 기대 후보 수, 기대 status/flag). CHK021 정책 (SPEC 5.5).
- check_against_golden: 분석 결과가 정답표를 어기는지 검사 (분류 엔진 아님).
- 정상 stub runner는 통과, 위반 stub(제안→Action, 없는 담당자·기한 생성)은 실패해야 한다.
"""
import copy

import pytest

from meeting_action_agent.analyst import analyze_transcript, validate_analysis
from meeting_action_agent.pipeline import extract_and_convert
from meeting_action_agent.store import ActionStore

MID = "20261110-01"
META = {"meeting_id": MID, "meeting_date": "2026-11-10", "participants": ["팀장", "김대리"]}

# (timestamp, 발언, 기대 유형, 후보 수, 기대 status, 기대 flag)
GOLDEN = [
    ("00:01:00", "김대리, 금요일까지 도면 수정해서 공유해주세요.", "지시·요청", 1, "신규", None),
    ("00:02:00", "제가 내일까지 정리해서 보내겠습니다.", "자발적 약속", 1, "신규", None),
    ("00:03:00", "다음 회의에서는 품질 문제도 다뤄보면 어떨까요?", "제안", 0, None, None),
    ("00:04:00", "도면 검토는 끝났나요?", "질문", 0, None, None),
    ("00:05:00", "김대리, 견적 오면 검토합시다.", "조건부", 1, "확인 필요", "조건부"),
    ("00:06:00", "김대리, 그 건은 좀 봐주면 좋겠네요.", "지시여부애매", 1, "확인 필요", "지시여부애매"),
]
TRANSCRIPT = [{"meeting_id": MID, "speaker": "팀장", "timestamp": ts, "text": t} for ts, t, *_ in GOLDEN]


def _cand(ts, text, itype, assignee, task, due, status, flag):
    c = {"meeting_id": MID, "assigner": "팀장", "assignee": assignee, "task": task, "due_date": due,
         "due_date_normalized": None, "evidence": text, "timestamp": ts, "status": status, "flags": []}
    if flag:
        c["flags"] = [flag]
        if flag == "조건부":
            c["flag_notes"] = {"조건부": {"note": "견적 도착 시"}}
    return {"instruction_type": itype, "candidate": c}


def good_analysis():
    g = {r[0]: r for r in GOLDEN}
    return {
        "meeting_id": MID,
        "key_discussions": [{"text": "도면·품질 논의", "evidence_timestamps": ["00:01:00"]}],
        "decisions": [],
        "open_issues": [{"text": "품질 문제 논의 제안", "evidence_timestamps": ["00:03:00"]},
                        {"text": "도면 검토 완료 여부", "evidence_timestamps": ["00:04:00"]}],
        "needs_confirmation": [],
        "action_candidates": [
            _cand("00:01:00", g["00:01:00"][1], "지시·요청", "김대리", "도면 수정 후 공유", "금요일까지", "신규", None),
            _cand("00:02:00", g["00:02:00"][1], "자발적 약속", "제가", "정리해서 전달", "내일까지", "신규", None),
            _cand("00:05:00", g["00:05:00"][1], "조건부", "김대리", "견적 검토", "미정", "확인 필요", "조건부"),
            _cand("00:06:00", g["00:06:00"][1], "지시여부애매", "김대리", "해당 건 확인", "미정", "확인 필요", "지시여부애매"),
        ],
    }


def check_against_golden(analysis):
    """정답표 위반 목록. 비어 있으면 통과."""
    errs = []
    by_ts = {}
    for item in analysis["action_candidates"]:
        by_ts.setdefault(item["candidate"]["timestamp"], []).append(item)
    for ts, text, itype, n, status, flag in GOLDEN:
        items = by_ts.get(ts, [])
        if len(items) != n:
            errs.append(f"{ts} {itype}: 후보 {len(items)}건 (기대 {n})")
            continue
        for it in items:
            c = it["candidate"]
            if it["instruction_type"] != itype:
                errs.append(f"{ts}: 유형 {it['instruction_type']} (기대 {itype})")
            if c["status"] != status or (flag and flag not in c["flags"]):
                errs.append(f"{ts}: status/flag 불일치")
            if c["due_date_normalized"] is not None:  # 환산은 코드 담당
                errs.append(f"{ts}: 환산 날짜 생성")
            if c["assignee"] not in text and c["assignee"] not in ("확인 필요",):
                errs.append(f"{ts}: 발언에 없는 담당자 {c['assignee']}")
            if c["due_date"] not in text and c["due_date"] not in ("미정", "확인 필요"):
                errs.append(f"{ts}: 발언에 없는 기한 {c['due_date']}")
    return errs


def test_golden_contract_passes_and_pipeline_creates_expected_actions(tmp_path):
    a = good_analysis()
    assert check_against_golden(a) == []
    analyze_transcript(TRANSCRIPT, lambda p: a)  # 검증 통과
    store = ActionStore(tmp_path / "actions.json")
    extract_and_convert(TRANSCRIPT, lambda p: a, store=store, metadata=META)
    acts = store.load_all()
    assert len(acts) == 4  # 지시1 + 약속1 + 조건부1 + 애매1 (제안·질문 0건)
    assert {x["timestamp"] for x in acts}.isdisjoint({"00:03:00", "00:04:00"})
    assert [x["status"] for x in acts] == ["신규", "신규", "확인 필요", "확인 필요"]


def test_proposal_or_question_as_action_fails_even_if_ambiguous():
    a = good_analysis()
    a["action_candidates"].append(
        _cand("00:03:00", GOLDEN[2][1], "지시여부애매", "확인 필요", "품질 문제 논의", "미정", "확인 필요", "지시여부애매"))
    assert any("00:03:00" in e for e in check_against_golden(a))  # CHK021: 명확한 제안은 애매 Action도 위반
    b = good_analysis()
    b["action_candidates"].append(_cand("00:04:00", GOLDEN[3][1], "지시·요청", "김대리", "검토", "미정", "신규", None))
    assert any("00:04:00" in e for e in check_against_golden(b))


def test_missing_candidate_and_fabrication_fail():
    a = good_analysis()
    del a["action_candidates"][0]  # 과소추출
    assert any("00:01:00" in e for e in check_against_golden(a))
    b = good_analysis()
    b["action_candidates"][0]["candidate"]["assignee"] = "박과장"  # 없는 담당자
    b["action_candidates"][0]["candidate"]["due_date"] = "다음 주 월요일"  # 없는 기한
    c = good_analysis()
    c["action_candidates"][0]["candidate"]["due_date_normalized"] = "2026-11-13"  # 환산 날짜 생성
    errs = check_against_golden(b)
    assert any("담당자" in e for e in errs) and any("기한" in e for e in errs)
    assert any("환산" in e for e in check_against_golden(c))
