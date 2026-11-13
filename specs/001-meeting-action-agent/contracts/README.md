# contracts/ — 데이터 계약 (JSON Schema)

> 기준: `../SPEC.md` 5장, `../../../CLAUDE.md` 데이터 인터페이스.
> **SPEC.md가 정본이다.** 이 폴더는 SPEC의 필드 정의를 기계가 검증할 수 있는 형태로 옮긴 것이며, 필드를 바꾸려면 SPEC.md를 먼저 수정한 뒤 여기를 맞춘다.
> JSON Schema Draft 2020-12. 모든 스키마는 `common.schema.json`의 정의를 `$ref`로 공유한다.

## 스키마 목록

| 파일 | 대상 | 근거 | SPEC 정의 여부 |
|---|---|---|---|
| `common.schema.json` | 공용 타입 (Timestamp, Status, Flag, EvidenceQuote 등) | 5.1~5.3 | 정의됨 |
| `utterance.schema.json` | 녹취 발언 1건 (A ↔ B 인터페이스) | 5.1 | 정의됨 |
| `transcript.schema.json` | 녹취록 파일 (발언 배열) | 5.1, 9장 | 정의됨 |
| `meeting-metadata.schema.json` | 회의 메타데이터 | 5.1 | 정의됨 (`speaker_map` 하위 필드는 제안) |
| `action.schema.json` | 저장된 Action | 5.2~5.6 | 정의됨 (`flag_notes`, `extra_evidence` 항목 구조는 제안) |
| `action-candidate.schema.json` | Agent가 내는 Action 후보 | 9장 | 구조는 제안 |
| `meeting-analysis.schema.json` | 회의 분석 결과 (요약·결정·미결·지시 후보) | 6.1, 7장, US-06 | 구조는 제안 |
| `followup-result.schema.json` | 이행점검 판정 1건 | 5.6, US-04 | 구조는 제안 |
| `review-result.schema.json` | Reviewer 판정 | 6.3 | 구조는 제안 |
| `review-queue-item.schema.json` | 사람 확인 큐 항목 | 6.4 | 구조는 제안 |

"제안"으로 표시한 항목은 SPEC에 필드 수준 정의가 없어 구현에 필요한 최소 구조를 둔 것이다. 확정하려면 SPEC.md에 반영해야 한다 (`../data-model.md` 8장).

## 스키마가 강제하는 SPEC 규칙

| 규칙 | 스키마 | SPEC |
|---|---|---|
| `timestamp`는 `HH:MM:SS` | 모든 Timestamp | US-01 |
| 녹취 발언에 정의 외 필드 금지 | `utterance` (`additionalProperties: false`) | 5.1 |
| `status: 완료`는 `approved_by_human` 필수 | `action` | US-05, 9장 |
| Agent는 `완료`로 전이 불가 | `action` history, `action-candidate` status enum, `followup-result` enum | 5.3, 9장 |
| `완료` 전이 history에는 승인 스냅샷(`approval`) 필수 | `action` history | 5.2, 5.6 |
| history 최초 항목(`from_status: null`)의 주체는 Agent (사람의 직접 생성 불가) | `action` history | 3.2, 5.2 |
| 완료 철회(`from_status: 완료`)는 사람만, `actor_name`과 철회되는 `approval` 필수 | `action` history | 5.6 |
| 재판정 항목(`kind: 재판정`)은 Agent, 근거 1개 이상과 사유 필수 | `action` history | 5.6 |
| Agent 출력에 `action_id`·`approved_by_human`·`history` 금지 | `action-candidate` | 9장 |
| 기한이 `미정`·`확인 필요`면 환산 날짜 null | `action`, `action-candidate` | 5.4 |
| `조건부`·`지시여부애매`면 status `확인 필요`, 조건부는 조건 기재 | `action-candidate` | 5.5 |
| 완료 후보·진행중·미완료는 근거 발언 필요 | `followup-result` | US-04 |
| 확신도 `하`면 `확인 필요` | `followup-result` | 5.6 |
| 기한변경제안은 새 기한 원문 필요 | `followup-result` | 5.6 |
| REJECT는 사유 필요, 재시도 `attempt` ≤ 2 | `review-result` | 6.3, 6.4 |
| 사람 확인 큐 항목은 `확인 필요` + `Reviewer미통과`, 폐기는 사람이 결정 | `review-queue-item` | 6.4 |

## 스키마로 표현할 수 없어 코드가 검증하는 규칙

JSON Schema는 문서 안의 형식과 조건만 검사한다. 아래는 다른 파일이나 상태와 대조해야 하므로 코드에서 검증한다 (tasks T-03, T-06).

| 규칙 | 담당 |
|---|---|
| 한 녹취 파일의 모든 발언이 같은 `meeting_id`이고 `timestamp`가 시간순 | 녹취 로더 |
| `timestamp`가 해당 회의의 실제 발언과 일치, `evidence`가 그 발언 `text`의 부분 문자열(공백·구두점 정규화 후), `speaker` 일치 | Reviewer 코드 검증기 (SPEC 6.3) |
| `action_id`가 저장소 전체에서 유일 | 저장 계층 |
| 상태 전이가 전이표 안에 있음, `완료`는 `완료 후보`에서만, 자기 전이는 상태 변경 경로에서 거부 | 상태 전이 모듈 (SPEC 5.6) |
| 로드 시 history의 모든 상태 변경 항목이 최초 생성 규칙·전이표를 따르고 서로 이어지며 마지막 상태가 현재 `status`와 같음 (재판정 항목은 별도 규칙) | 저장 계층 (SPEC 9장, T-03-14) |
| `kind: 재판정` 항목은 `from_status`와 `to_status`가 같고 Action의 현재 상태와 일치, 재판정은 상태를 바꾸지 않음 | 저장 계층 (SPEC 5.6) |
| `완료` 전이는 그 요청에 새로 제출된 승인으로만 (철회 후 남은 `approved_by_human`은 승인이 아님), 과거 history·승인 스냅샷은 삭제·덮어쓰기 금지 | 저장 계층 (SPEC 5.6, 9장) |
| `due_date` 원문에서 `due_date_normalized`가 올바르게 환산됨 | 기한 환산 규칙 (SPEC 5.4) |
| `assignee`가 녹취에 실재, 호칭 제거, 동명이인 판단 | 값 규칙 + Reviewer |
| 파일을 직접 편집해 `완료`로 바꾼 경로 | 저장소 로드 시 재검증 (SPEC 9장) |

## 예시와 검증

- `examples/valid/` — 스키마를 통과해야 하는 예시. SPEC의 US-02, US-03, 8장 시나리오 값을 사용했다 (회의는 모두 가상).
- `examples/invalid/` — 스키마가 거부해야 하는 예시. 각 파일이 어느 스키마로 검증되는지는 `examples/invalid/_manifest.json`에 있다.
  SPEC의 실패 조건(승인 없는 `완료`, Agent의 `완료` 설정, 근거 없는 환산 날짜, 근거 없는 판정 등)에 대응한다.

전체 검증은 스크립트 하나로 실행한다 (`jsonschema`, `referencing` 필요. 자세한 설치는 `../quickstart.md`).

```powershell
python specs\001-meeting-action-agent\contracts\validate_examples.py
```

스키마 하나만 코드에서 직접 쓰는 방법:

```python
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in Path("contracts").glob("*.schema.json")}
registry = Registry().with_resources(
    (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012)) for s in schemas.values()
)
validator = Draft202012Validator(schemas["action.schema.json"], registry=registry)
validator.validate(json.loads(Path("contracts/examples/valid/action.json").read_text(encoding="utf-8")))
```

`$id`는 로컬 식별자(`https://meeting-action-agent.example/...`)이며 실제로 접속하지 않는다. 구현 단계(T-00-8)에서 `tests/`의 계약 테스트로 옮겨 `examples/valid`는 통과, `examples/invalid`는 거부되는지 자동 검사한다.
