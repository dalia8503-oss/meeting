# Data Model — Meeting Action Agent

> 정본: `SPEC.md` 5장. 이 문서는 구현용 정리이며, 충돌하면 SPEC.md가 우선한다.
> 필드 변경은 SPEC.md를 먼저 수정한다 (0.CLAUDE.md). 기계 검증용 스키마는 `contracts/`에 있다.
> 작성일: 2026-11-11 / 상태: 초안

## 1. 엔티티 개요

```text
MeetingMetadata ─1:1─ Transcript ─1:N─ Utterance
       │                                  ▲ (근거로 참조: meeting_id + timestamp)
       │1:1                               │
MeetingAnalysis ─1:N─ SummaryItem ────────┤
       │1:N                               │
       ▼                                  │
ActionCandidate ──Reviewer PASS──▶ Action ─┤ evidence / extra_evidence / history.evidence
       │ REJECT 2회 초과                  │ 1:N
       ▼                                  ▼
ReviewQueueItem                        HistoryEntry
                                          ▲
FollowupResult (신규 회의 vs 이전 Action) ─┘ Reviewer PASS 후 상태 변경으로 기록
```

- Utterance가 모든 근거의 원천이다. Action·요약·이행 판정은 Utterance를 `(meeting_id, timestamp)`로 가리키며, Reviewer 코드 검증기가 실제 존재 여부를 대조한다.
- Action은 Agent가 직접 저장하지 않는다. `ActionCandidate` → Reviewer PASS → 저장 계층이 `action_id`, `field_source`, `history`를 채워 `Action`으로 변환한다.

## 2. 엔티티 상세

계약: `contracts/<스키마>`. "제안"은 SPEC에 필드 수준 정의가 없어 추가한 구조다 (8장).

### 2.1 Utterance (`utterance.schema.json`) — SPEC 5.1

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| meeting_id | string | O | 예: `20261110-01` |
| speaker | string | O | 화자. speaker_map이 없으면 라벨 그대로 |
| timestamp | `HH:MM:SS` | O | 발언 **시작** 시점. 시각(time-of-day)이 아니라 **녹음 시작 기준 경과시간**이다 (HH는 00~23로 제한하지 않음, MM·SS는 00~59) |
| text | string | O | 발언 원문 |
| low_confidence | boolean | | STT 신뢰도 낮음. 기본 false |

정의 외 필드는 허용하지 않는다.

### 2.2 MeetingMetadata (`meeting-metadata.schema.json`) — SPEC 5.1

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| meeting_id | string | O | |
| meeting_date | `YYYY-MM-DD` | O | 기한 환산 기준 |
| title | string | | |
| participants | string[] | | 담당자 대조용 (동명이인, `참석자외`) |
| speaker_map | 라벨 → {name, position?} | | 없으면 지시자 `확인 필요` |

### 2.3 Action (`action.schema.json`) — SPEC 5.2

| 필드 | 타입 | 설명 |
|---|---|---|
| action_id | `A-NNN` | 저장소 전체에서 유일. 코드가 발급 |
| meeting_id | string | 발생한 회의 |
| assigner | string | 지시자. 불명확 시 `확인 필요` |
| assignee | string | 담당자. 불명확 시 `확인 필요`. 호칭 제거 |
| task | string | 업무 내용 |
| due_date | string | 기한 **원문**. `미정` 또는 `확인 필요` 가능 |
| due_date_normalized | date \| null | 회의일 기준 환산. 근거 없으면 null |
| evidence | string | 근거 발언 원문 |
| extra_evidence | EvidenceQuote[] | 추가 근거 (항목 구조는 제안) |
| timestamp | `HH:MM:SS` | 근거 타임스탬프 |
| status | Status | 아래 2.5 |
| flags | Flag[] | 아래 2.6 |
| flag_notes | Flag → {note, evidence?} | 플래그 부가 정보 (제안) |
| field_source | 필드 → `녹취` \| `사람 보강` | 키 없으면 `녹취` |
| approved_by_human | Approval \| null | **현재(최신) 승인**. `완료`의 필수 조건. 철회 후에도 보존, 재완료 시 새 승인으로 갱신 |
| history | HistoryEntry[] | 모든 상태 변경과 재판정 (시간순, 삭제·덮어쓰기 금지) |

**Approval**: `approver`, `approved_at`(date-time), `evidence`{`meeting_id`, `timestamp`}. `approved_by_human`(현재 값)과 `history[].approval`(과거 스냅샷)이 같은 구조를 쓴다.

**HistoryEntry**: `at`(date-time), `actor`(`Agent`/`사람`), `from_status`(최초 생성이면 null), `to_status`, `kind`(선택: `상태변경`(기본) / `재판정`), `actor_name`(선택: 사람이 한 변경의 수행자), `approval`(선택: Approval 스냅샷), `evidence`(EvidenceQuote[], 선택), `note`(선택).

| 항목 종류 | 규칙 |
|---|---|
| 최초 생성 (`from_status: null`) | `actor`는 Agent (사람의 직접 생성은 MVP 제외, 사람의 수정·보강은 최초 생성 주체를 바꾸지 않음) |
| `완료`로 전이 | `actor`는 `사람`, `approval` 필수 (그 시점의 새 승인 스냅샷) |
| 완료 철회 (`from_status: 완료`) | `actor`는 `사람`, `actor_name`과 철회되는 `approval` 필수. `approved_by_human`은 지우지 않는다 |
| 재판정 (`kind: 재판정`) | `actor`는 Agent, `from_status == to_status`(코드 검증), `evidence` 1개 이상과 `note` 필수. 상태는 바뀌지 않는다 |

**EvidenceQuote**: `meeting_id`, `timestamp`, `speaker`, `text` (모두 필수). 이행점검 근거는 신규 회의의 발언이므로 `meeting_id`를 포함한다.

**불변 규칙**
- 같은 회의에서 동일 담당자·업무가 반복 언급되면 하나의 Action으로 합치고 추가 발언은 `extra_evidence`에 넣는다.
- 이전 회의 Action의 재언급은 신규 Action이 아니라 이행점검 대상이다.
- 한 발언에 지시가 여럿이면 Action을 분리한다 (같은 근거 타임스탬프 공유 가능). 복수 담당자도 인원별로 분리하고 `공동` 플래그를 붙인다.
- 지시가 여러 발언에 걸쳐 있으면 모든 발언을 근거로 남긴다.

### 2.4 ActionCandidate (`action-candidate.schema.json`) — 제안

Agent(Meeting Analyst / Action Manager)가 내는 후보. Action에서 `action_id`, `approved_by_human`, `history`, `field_source`를 뺀 형태이며 `status`에 `완료`를 쓸 수 없다. Agent가 사람 전용 필드를 채우려 하면 스키마 검증에서 거부된다.

### 2.5 Status (`common.schema.json`) — SPEC 5.3

| 값 | 표시 | 설정 주체 |
|---|---|---|
| 신규 | 🔵 | Agent |
| 진행중 | 🟠 | Agent |
| 완료 후보 | 🟢 | Agent |
| 완료 | ✅ | **사람만** |
| 미완료 | 🔴 | Agent |
| 확인 필요 | 🟡 | Agent |

저장 값에는 이모지를 넣지 않는다. 이모지는 Dashboard 표시용이다.

### 2.6 Flag (`common.schema.json`)

| 값 | 의미 | 출처 |
|---|---|---|
| 조건부 | 조건이 붙은 지시. 조건은 `flag_notes`에 기재 | 5.5 |
| 지시여부애매 | 지시인지 애매. 사람이 확인 | 5.5 |
| Reviewer미통과 | 재시도 2회 후에도 REJECT. 반려 사유 첨부 | 6.4 |
| 기한경과 | 진행중인데 기한이 지남 | 5.6 |
| 기한변경제안 | 기한 변경 발언 발견. 새 기한 원문은 `flag_notes`에 기재, 반영은 사람 | 5.6 |
| 팀지칭 | 담당자가 팀·부서 | 5.4 |
| 공동 | 복수 담당자 공동 책임 | 5.4 |
| 참석자외 | 담당자가 제공된 참석자 명단에 없음 | 5.4 |

### 2.7 MeetingAnalysis (`meeting-analysis.schema.json`) — 제안

Meeting Analyst 출력. `key_discussions`, `decisions`, `open_issues`, `needs_confirmation`은 각각 `{text, evidence_timestamps(1개 이상)}` 목록이고, `action_candidates`는 `{instruction_type, candidate}` 목록이다. 지시가 0건이면 빈 배열이며 오류가 아니다 (US-02).

`instruction_type`은 SPEC 5.5 중 Action을 만드는 유형(`지시·요청`, `자발적 약속`, `조건부`, `지시여부애매`)이다. 제안·질문은 Action이 아니라 `open_issues`로, 과거 보고는 이행점검 근거 후보로만 쓰인다.

### 2.8 FollowupResult (`followup-result.schema.json`) — 제안

이행점검에서 Action 1건당 1개.

| 필드 | 설명 |
|---|---|
| meeting_id | 점검에 사용한 **신규** 회의 |
| action_id | 점검 대상 Action |
| proposed_status | `완료 후보` / `진행중` / `미완료` / `확인 필요` (`완료` 불가) |
| confidence | `상` / `중` / `하` |
| evidence | 신규 회의 근거 발언. 언급 없음이면 빈 배열 |
| flags_to_add | `기한경과`, `기한변경제안` |
| proposed_due_date | 기한변경제안일 때 새 기한 원문 (기존 `due_date`는 덮어쓰지 않음) |
| reason | 판정 사유 |

### 2.9 ReviewResult / ReviewQueueItem — 제안

- **ReviewResult**: 대상 1건에 대한 `verdict`(PASS/REJECT), `reasons`(`code`, `detail`), `attempt`(0~2). REJECT면 사유가 1개 이상 필요하다. 이유 코드는 SPEC 6.3의 PASS/REJECT 기준을 코드화한 것이다: `필수필드누락`, `타임스탬프불일치`, `화자불일치`, `인용불일치`, `허위담당자`, `허위기한`, `허위환산날짜`, `지시아님`, `근거없음`, `과소추출`, `관련성없음`, `판정비도출`, `완료설정`.
- **ReviewQueueItem**: 재시도 2회 후에도 REJECT된 건. 사람이 유지·수정·보강해도 Action의 최초 생성 주체(history 첫 항목의 `actor`)는 Agent로 유지한다. 대상(`candidate` 또는 `followup`), REJECT 이력, `queued_at`, `resolution`(사람이 결정하기 전 null; `유지`/`수정`/`폐기`와 결정자·시각)을 가진다. 폐기도 사람만 한다 (SPEC 6.4).

## 3. 상태 전이 (SPEC 5.6)

| 전이 | 주체 | 조건 |
|---|---|---|
| (없음) → 신규 / 확인 필요 | Agent | 추출 + Reviewer PASS. 담당·기한이 `확인 필요`면 `확인 필요`, 기한 `미정`만으로는 `신규` |
| 신규·진행중·확인 필요·미완료 → **현재와 다른** 진행중 / 미완료 / 확인 필요 / 완료 후보 | Agent | 이행 판정 + Reviewer PASS |
| 완료 후보 → **완료** | **사람** | `[완료]` + `approved_by_human` |
| 완료 후보 → 진행중 | 사람 | `[계속 진행]` |
| 완료 후보 → 확인 필요 | 사람 | `[확인 필요]` |
| 완료 → 진행중 | **사람만** | 완료 철회. 철회자·시각·철회되는 승인 스냅샷을 history에 기록, `approved_by_human` 보존 |
| 위 표에 없는 전이, Agent의 `완료` 설정 | — | **차단** |

- `완료` Action은 이행점검 대상에서 제외한다 (철회되어 진행중이 되면 다시 대상).
- 모든 전이는 `history`에 기록한다.
- `완료` Action은 이행점검·재판정 대상에서 제외한다 (`record_reassessment` 거부).
- **자기 전이는 상태 전이가 아니다.** 상태 변경 경로(`change_status`)는 이를 거부하고, 상태가 유지되는 재판정은 별도 경로(`record_reassessment`)가 `kind: 재판정` history 항목으로 남긴다.
- **완료 후보는 사람의 최종 판단 대기 상태다.** Agent는 상태를 바꿀 수 없고 재판정(추가 근거) 기록만 남긴다. 완료 후보도 이행점검 대상이다.
- 철회 후 남아 있는 `approved_by_human`은 재완료의 승인으로 인정하지 않는다. `완료` 전이는 그 요청에 새로 제출된 승인으로만 가능하다. 이전 승인(현재 값·history 스냅샷)과 승인자·시각·근거가 모두 같은 값의 재제출도 새 승인으로 인정하지 않는다.
- 이행 판정 → 상태 매핑: 완료형 발언 → `완료 후보`, 진행·부분 완료 → `진행중`, 미수행·지연 명시 → `미완료`, 언급 없음·확신도 하·`low_confidence`만 근거·상충·철회 → `확인 필요`.

## 4. 값 규칙 요약 (SPEC 5.4)

**담당자**

| 상황 | assignee |
|---|---|
| 이름·직책 명시 | 발언 표기 그대로, 호칭(씨·님)만 제거 |
| 호칭만 있고 동명이인 후보 복수 | `확인 필요` |
| 팀·부서 | 팀 이름 + `팀지칭` |
| 복수 담당자 | 인원별 Action 분리 + `공동` |
| 자기 할당 | 화자 실명(speaker_map). 매핑 없으면 `확인 필요` |
| 대명사·생략 | `확인 필요` |

**기한**

| 상황 | due_date | due_date_normalized |
|---|---|---|
| 날짜·요일 명시 | 원문 | 환산 가능 시만 값, 아니면 null |
| "다음 회의까지", 범위 표현 | 원문 | null |
| 명시적 미정 | `미정` | null |
| 해석 불가·언급 없음 | `확인 필요` | null |

요일 단독 표현은 회의일 이후 처음 오는 해당 요일로 환산하고, 회의일과 같은 요일이면 환산하지 않는다 (null). 시각(오전·오후)은 `due_date` 원문에만 보존한다.

## 5. 검증 계층

| 계층 | 시점 | 검증 내용 | 수단 |
|---|---|---|---|
| ① 스키마 | Agent 출력 수신, 저장·로드 | 필드·형식·enum, 사람 전용 필드와 `완료` 차단, 조건부 규칙 | `contracts/` JSON Schema |
| ② 값 규칙 | 후보 생성 후 | 환산 날짜, 호칭 제거, status 결정 | 규칙 코드 (T-03-4) |
| ③ Reviewer 코드 검증 | 저장 전 | timestamp·speaker·인용문이 실제 발언과 일치 | 코드 (T-06-2) |
| ④ Reviewer 의미 검증 | 저장 전 | 담당자·기한·업무가 근거와 일치, 지시 여부 | Reviewer Agent (읽기 전용) |
| ⑤ 상태 전이·완료 가드 | 상태 변경 시 | 전이표, 자기 전이 거부, 새로 제출된 `approved_by_human` | 저장 계층 (T-03-3) |

①~⑤ 중 하나라도 실패하면 저장하지 않는다. **로드 시 history 검증(⑤)**: 상태 변경 항목(`kind` 없음 또는 `상태변경`)은 최초 생성 규칙과 전이표(`is_allowed`)를 따르고, 각 항목의 이전 상태가 직전 상태와 이어지며, 마지막 상태가 Action의 `status`와 같아야 한다. 재판정 항목(`kind: 재판정`)은 상태 전이가 아니므로 제외하고, 이전·이후 상태가 같고 그 시점의 현재 상태와 일치하는지만 본다. 위반은 `TransitionError`(`완료` 승인 불일치는 `CompletionGuardError`)이다.

①②③ 실패는 Action Manager 재분석(최대 2회), 초과 시 사람 확인 큐로 보낸다.

## 6. 저장 레이아웃 (제안)

| 경로 | 내용 | 계약 | 쓰기 규칙 |
|---|---|---|---|
| `audio/<meeting_id>.<ext>` | 원본 녹음 | — | **신규 생성만**, 수정·삭제 차단 |
| `transcripts/original/<meeting_id>.json` | 원본 녹취 (Utterance 배열) | transcript | **신규 생성만**, 수정·삭제 차단 |
| `transcripts/` 그 외 하위 | 파생 녹취 (보정본 등) | transcript | 허용 |
| `meetings/<meeting_id>.meta.json` | 회의 메타데이터 | meeting-metadata | 허용 |
| `meetings/<meeting_id>.analysis.json` | 회의 분석 결과 | meeting-analysis | 허용 |
| `actions/` | 확정 Action List (JSON 정본) + Markdown 파생 보고서 | action | 저장 계층만 쓴다 |
| `actions/review_queue/` | 사람 확인 큐 | review-queue-item | 허용 |

`actions/` 안의 파일 분할 방식(회의별 목록 vs 누적 목록)은 미정이다 (8장). 정본은 JSON이고 Markdown은 보고용 파생물이라 다시 읽지 않는다.

## 7. 요구사항 추적

| 사용자 스토리 | 관련 엔티티 | 실패 조건을 막는 계층 |
|---|---|---|
| US-01 녹음 분석 | Utterance, MeetingMetadata | ① 타임스탬프·필수 필드. 원본 해시는 파일 보호 |
| US-02 지시 추출 | MeetingAnalysis, ActionCandidate, Action | ③④ 근거·지시 여부. 재현율은 정답표 테스트 |
| US-03 불명확 정보 | Action(`assignee`, `due_date`) | ② 값 규칙, ③④ 허위 담당·기한 |
| US-04 이행점검 | FollowupResult, HistoryEntry | ① 근거·확신도 규칙, ③④ 근거 대조 |
| US-05 사람 승인 | Action(`approved_by_human`), HistoryEntry | ① ⑤ 완료 가드 |
| US-06 회의 요약 | MeetingAnalysis(SummaryItem) | ③④ 근거 타임스탬프 대조 |

## 8. SPEC 보완이 필요한 항목

SPEC에 필드 수준 정의가 없어 이 문서와 `contracts/`에서 임시로 정한 부분이다. 승인되면 SPEC.md 5장에 반영하고, 반려되면 계약을 수정한다.

| # | 항목 | 임시 결정 | 이유 |
|---|---|---|---|
| 1 | `extra_evidence` 항목 구조 | EvidenceQuote(`meeting_id`, `timestamp`, `speaker`, `text`) | Reviewer가 추가 근거도 시각·화자·인용으로 대조해야 함 |
| 2 | 근거 발언의 `speaker` 필드 | Action 본문에는 없음. `timestamp`로 녹취에서 조회해 대조 | SPEC 6.3은 `speaker` 일치를 검증하지만 Action 필드에 화자가 없음. 필드를 추가할지 결정 필요 |
| 3 | `flags` 부가 정보 | `flag_notes` 필드 추가 | `조건부`의 조건, `기한변경제안`의 새 기한을 기재하라고 하나 저장 위치가 없음 |
| 4 | 이행점검 근거의 저장 위치 | `history[].evidence` | SPEC은 "history에 근거 포함"만 정의 |
| 5 | `speaker_map` 값 구조 | `{name, position?}` | SPEC은 "실명·직책 매핑"만 정의 |
| 6 | MeetingAnalysis, FollowupResult, ReviewResult, ReviewQueueItem 구조 | 2.7~2.9 | SPEC은 항목과 PASS/REJECT 기준만 정의 |
| 7 | `철회` 유형 처리 | 신규 Action 후보가 아니라 이행점검에서 기존 Action을 `확인 필요`로 전환 | SPEC 5.5 "기존 Action을 확인 필요로 전환". 같은 회의 안에서 지시 후 철회가 나오는 경우는 미정 |
| 8 | Action List 파일 단위 | 미정 | plan.md 8장 #7(이전 Action List 지정 방식)과 함께 결정 |
| 9 | `meeting_id` 형식 | 제한 없음 (예시만 있음) | SPEC은 `20261110-01`을 "예"로만 제시 |
| 10 | 연도 없는 날짜("11/12")의 연도 | 미정 | 연말 회의에서 "1월 5일"이 나오면 다음 해인지 SPEC에 없음 |
| 11 | 재판정 기록 방식 | history 항목(`kind: 재판정`, 이전·이후 상태 동일). **결정됨 → SPEC 5.6 반영** | 2026-11-11 정책 결정 |
| 12 | 승인 스냅샷·철회자 필드 | `HistoryEntry.approval`, `actor_name`. **결정됨 → SPEC 5.2, 5.6 반영** | 2026-11-11 정책 결정 |
| 13 | 큐에서 사람 수정 시 최초 생성 주체 | Agent 유지. **결정됨 → SPEC 5.2 반영**. 큐 처리 후 상태·폐기 이력은 CHK015로 여전히 미결 | 2026-11-11 정책 결정 |
| 14 | 완료 Action에 재판정 기록 시도 | **결정됨**: 완료 Action은 이행점검·재판정 대상에서 제외하므로 `record_reassessment`는 거부(`TransitionError`) | 2026-11-11 정책 결정 |
| 15 | 재판정·철회 입력 누락(근거·사유·철회자 이름 없음)의 오류 종류 | 미정 (스키마는 거부, 예외 타입은 구현에서 정함) | T-03-3 착수 전 확인 |
| 16 | FollowupResult의 `confidence`·`proposed_status` 보존 방식 | 미정 (재판정 history에는 `evidence`·`note`만 남음) | T-08/T-09 단계에서 결정 |
