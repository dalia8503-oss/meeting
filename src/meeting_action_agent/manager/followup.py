"""T-08 이전 Action 이행점검 (domain slice) — Action Manager의 follow-up 책임.

흐름: ActionStore에서 이전 회의의 미완료 Action 조회 → Action별로 runner(LLM) 호출 → FollowupResult
      → 코드 검증(계약·완료 가드·근거 실재·질문/부정 근거 차단·이름 추측 차단) → (선택) 저장.

원칙
- Action의 status·history·파일을 바꾸지 않는다 (읽기 전용). 상태 변경·재판정 기록은 T-09.
- 계약(followup-result.schema.json)의 proposed_status 값만 쓴다. 새 enum 없음. '완료'는 낼 수 없다.
- 판정(의미 판단)은 runner가 한다. 이 모듈의 코드는 거부 방향의 가드만 둔다(키워드로 완료를 만들지 않는다).
- 검증을 통과하지 못한 Action 판정은 `rejected`로 돌려주고 저장하지 않는다 (보완·추측 없음).
- Reviewer(LLM) 연동·재분석 루프는 followup_apply(T-09-7)가 한다. 여기서는 Reviewer의 코드 검증에 해당하는 근거 실재 대조만 한다.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Union

from meeting_action_agent.contracts import (
    CONTRACTS_DIR,
    SchemaValidationError,
    receive_agent_output,
    validate,
)
from meeting_action_agent.speakers import confirmed_names, speaker_context_text
from meeting_action_agent.state import Status

# 완료 근거가 될 수 없는 발언 (거부 가드 전용). 완료 판정을 만드는 데는 쓰지 않는다.
_QUESTION = re.compile(r"[?？]|나요|까요|ㅂ니까|언제|어떻게")
_NEGATION = re.compile(r"못\s?(했|하|함|끝)|아직|않|안\s?(했|하|됐|됨)|미뤄|지연")


class FollowupValidationError(ValueError):
    """FollowupResult가 검증을 통과하지 못함. 저장하지 않는다."""


class NoPreviousActionsSpecified(ValueError):
    """점검할 이전 Action List가 없다. 점검을 시작하지 않는다 (T-08-1)."""


@dataclass(frozen=True)
class FollowupReport:
    results: tuple                 # 검증 통과한 followup-result dict들
    rejected: tuple = ()           # 검증 실패 {action_id, reason} (저장 안 함, 사람 확인 대상)
    targets: tuple = ()            # 점검 대상 action_id들
    saved_path: Optional[Path] = field(default=None)


def select_followup_targets(store, current_meeting_id: str,
                            previous_meeting_ids: Optional[Sequence[str]] = None) -> list:
    """현재 회의가 아닌 이전 회의의 `완료`가 아닌 Action (완료 후보 포함, SPEC 5.6·T-08-2).

    previous_meeting_ids가 주어지면(사용자 지정) 그 회의의 Action만 대상으로 한다. 시리즈 자동 추론 없음.
    """
    allowed = set(previous_meeting_ids) if previous_meeting_ids else None
    return [
        a for a in store.load_all()
        if a["meeting_id"] != current_meeting_id
        and a["status"] != Status.DONE.value
        and (allowed is None or a["meeting_id"] in allowed)
    ]


def build_prompt(action: Mapping, transcript: Sequence[Mapping], retry: Optional[Mapping] = None,
                 speaker_map: Optional[Mapping] = None) -> str:
    """retry={"previous_result": 1차 FollowupResult, "reviewer_reasons": REJECT reasons} (T-09-7 재분석 1회)."""
    schema = (CONTRACTS_DIR / "followup-result.schema.json").read_text(encoding="utf-8")
    payload = {
        "action": {k: action[k] for k in ("action_id", "meeting_id", "assignee", "task", "due_date",
                                          "status", "evidence", "timestamp")},
        "transcript": list(transcript),
    }
    retry_text = ""
    if retry is not None:
        retry_text = (
            "\n## 재분석 참고정보 (Reviewer REJECT)\n"
            "아래는 1차 판정과 Reviewer의 REJECT 사유다. 재분석의 참고정보일 뿐이며 "
            "evidence·status·flag의 근거로 사용하지 않는다. 최종 evidence는 반드시 transcript 원문에서만 복사한다.\n"
            + json.dumps({"previous_result": retry["previous_result"], "reviewer_reasons": retry["reviewer_reasons"]},
                         ensure_ascii=False, indent=1) + "\n"
        )
    return (
        "# 이행점검 (Action Manager)\n"
        "이전 회의의 Action 1건이 신규 회의 녹취에서 어떻게 언급되는지 판정한다.\n"
        "- proposed_status는 완료 후보/진행중/미완료/확인 필요 중 하나다. '완료'는 절대 내지 않는다.\n"
        "- 완료 후보는 해당 업무를 했다는 완료형 발언이 있을 때만 가능하다. 질문·부정·미수행·진행 중 발언은 완료 근거가 아니다.\n"
        "- 같은 단어가 있어도 다른 업무(예: 다른 도면)일 수 있다. 같은 업무인지 불명확하면 확신도 하 + 확인 필요.\n"
        "- 언급이 없으면 확인 필요 + 근거 빈 배열.\n"
        "- evidence는 transcript의 발언을 (meeting_id, timestamp, speaker, text) 그대로 복사한다. speaker_N을 이름으로 바꾸지 않는다.\n"
        "- 이름 비교에서 띄어쓰기 차이(김 대리 / 김대리)는 같은 이름으로 본다. 글자가 다르면 다른 사람이다.\n"
        "- 담당자 이름은 발언 텍스트에 있거나 사용자가 확인한 speaker_map에 있을 때만 근거로 쓴다. 추측하지 않는다.\n"
        "- 완료 후보는 담당자 본인의 완료 발언이어야 한다. speaker_map이 없거나 그 화자가 미확인·다른 사람이면 완료 후보를 내지 않는다.\n\n"
        f"## 출력 스키마 (followup-result.schema.json)\n{schema}\n\n"
        "## INPUT\n" + json.dumps(payload, ensure_ascii=False, indent=1)
        + "\n" + speaker_context_text(speaker_map) + retry_text + "\nFollowupResult JSON 하나만 출력하라."
    )


def _squash(text: str) -> str:
    return "".join(str(text).split())


def _check_evidence(result: Mapping, action: Mapping, transcript: Sequence[Mapping],
                    speaker_map: Optional[Mapping] = None) -> None:
    originals = {(u["meeting_id"], u["timestamp"], u["speaker"], u["text"]) for u in transcript}
    for q in result["evidence"]:
        if (q["meeting_id"], q["timestamp"], q["speaker"], q["text"]) not in originals:
            raise FollowupValidationError(
                f"근거가 현재 transcript의 원문과 다름 ({q['timestamp']} {q['speaker']}): 추측·보완 금지")

    if result["proposed_status"] != Status.DONE_CANDIDATE.value:
        return
    for q in result["evidence"]:
        if _QUESTION.search(q["text"]):
            raise FollowupValidationError(f"질문 발언은 완료 근거가 아니다 ({q['timestamp']})")
        if _NEGATION.search(q["text"]):
            raise FollowupValidationError(f"미수행·지연 발언은 완료 근거가 아니다 ({q['timestamp']})")
    # 담당자 본인의 완료 발언이어야 한다 (fail-closed). 본인 확인 근거는 셋 중 하나뿐이다:
    #   발언 텍스트에 담당자 이름 / speaker가 이미 담당자 이름 / 사용자가 확인한 speaker_map이 담당자로 연결.
    # speaker_map이 없거나(미확인) 다른 사람이면 speaker_N 라벨만으로는 담당자 본인을 증명할 수 없다.
    assignee = action.get("assignee")
    if not assignee or assignee == "확인 필요":
        return  # 담당자 미확정은 Safety Guard(CHK020)가 완료 후보를 막는다
    names = confirmed_names(speaker_map)
    who = _squash(assignee)  # 띄어쓰기만 다른 표기(김 대리/김대리)는 같은 이름으로 본다. 다른 글자는 다른 사람이다.
    self_confirmed = any(
        who in _squash(q["text"]) or _squash(q["speaker"]) == who or _squash(names.get(q["speaker"], "")) == who
        for q in result["evidence"])
    if not self_confirmed:
        raise FollowupValidationError("담당자 본인의 완료 발언임을 확인할 수 없다 (사용자 확인 화자 없음·미확인·다른 사람)")


def validate_followup(result, action: Mapping, transcript: Sequence[Mapping],
                      speaker_map: Optional[Mapping] = None) -> None:
    """완료 가드·스키마(receive_agent_output) → meeting_id/action_id → 근거 실재·안전 가드."""
    receive_agent_output("followup-result", result)  # '완료'면 CompletionGuardError
    meeting_id = transcript[0]["meeting_id"]
    if result["meeting_id"] != meeting_id:
        raise FollowupValidationError(f"meeting_id가 신규 회의와 다름: {result['meeting_id']} != {meeting_id}")
    if result["action_id"] != action["action_id"]:
        raise FollowupValidationError(f"action_id 불일치: {result['action_id']} != {action['action_id']}")
    _check_evidence(result, action, transcript, speaker_map)


def check_previous_actions(
    transcript: Sequence[Mapping],
    runner: Callable[[str], Union[str, dict]],
    *,
    store,
    previous_meeting_ids: Optional[Sequence[str]] = None,
    meetings_dir=None,
    speaker_map: Optional[Mapping] = None,
) -> FollowupReport:
    """현재 Transcript vs 이전 Action → FollowupResult 목록. Action은 변경하지 않는다.

    '완료' 제안(CompletionGuardError)은 안전 위반이라 즉시 전파한다. 그 외 검증 실패는 해당 Action만
    rejected로 두고 나머지는 계속 판정한다. meetings_dir가 있으면 통과분을 create-only로 저장한다.
    """
    validate("transcript", list(transcript))
    ids = {u["meeting_id"] for u in transcript}
    if len(ids) != 1:
        raise FollowupValidationError(f"transcript meeting_id가 하나가 아님: {sorted(ids)}")
    current = next(iter(ids))

    targets = select_followup_targets(store, current, previous_meeting_ids)
    if not targets and not [a for a in store.load_all() if a["meeting_id"] != current]:
        raise NoPreviousActionsSpecified("점검할 이전 Action List가 없다. 이전 회의 Action을 지정하라.")

    results, rejected = [], []
    for action in targets:
        raw = runner(build_prompt(action, transcript, speaker_map=speaker_map))
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
            validate_followup(result, action, transcript, speaker_map)
        except (json.JSONDecodeError, SchemaValidationError, FollowupValidationError) as e:
            rejected.append({"action_id": action["action_id"], "reason": str(e)})
            continue
        results.append(result)

    saved = save_followups(results, current, meetings_dir) if meetings_dir is not None and results else None
    return FollowupReport(tuple(results), tuple(rejected), tuple(a["action_id"] for a in targets), saved)


def save_followups(results: Sequence[Mapping], meeting_id: str, meetings_dir) -> Path:
    """meetings/<meeting_id>.followups.json 으로 저장한다 (create-only, 한 파일 원자 기록).

    저장 정책은 data-model 8장 #16에서 확정 (immutable snapshot, create-only): 전부 검증 후에만 쓰고,
    tmp 작성 → os.link(대상이 있으면 FileExistsError)로 덮어쓰기를 막는다. 부분 저장 없음.
    """
    for r in results:
        receive_agent_output("followup-result", r)
        if r["meeting_id"] != meeting_id:
            raise FollowupValidationError(f"meeting_id 불일치: {r['meeting_id']} != {meeting_id}")
    path = Path(meetings_dir) / f"{meeting_id}.followups.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(list(results), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.link(tmp, path)  # 이미 있으면 FileExistsError
    finally:
        tmp.unlink(missing_ok=True)
    return path
