# CLAUDE.md — Meeting Action Agent

회의 녹음을 분석해 지시사항을 **녹취 근거 기반**으로 정리하고, 이후 회의에서 이행 여부까지 추적하는 AI Agent.
상세 요구사항은 `specs/001-meeting-action-agent/SPEC.md`를 따른다. 이 파일은 모든 작업에서 항상 지켜야 할 규칙만 담는다.

## 절대 원칙 (위반 시 테스트 실패)

1. 녹취에 없는 사실(발언·담당자·기한·결정)을 만들지 않는다.
2. 담당자가 불명확하면 `담당자 확인 필요`로 표시한다. 임의로 추정하지 않는다.
3. 기한이 불명확하면 `확인 필요`(또는 `미정`)로 표시한다. 임의로 추정하지 않는다.
4. 모든 지시사항(Action)에 녹취 근거(원문 발언)와 타임스탬프(`HH:MM:SS`)를 남긴다.
5. Agent는 Action을 `완료`로 처리하지 않는다. 완료 근거를 발견하면 `완료 후보`로 제안만 하고, 사람이 최종 승인해야 `완료`가 된다.
6. 원본 녹음파일과 원본 녹취록은 수정·삭제하지 않는다.

## 보호 대상 (읽기 전용)

- `audio/**`
- `transcripts/original/**`

파생 결과는 `transcripts/`의 다른 하위 경로, `meetings/`, `actions/`에만 쓴다.
`.claude/settings.json`의 deny 규칙과 Hook으로 보호하며, 이를 우회하지 않는다.

## 프로젝트 구조

```text
meeting-action-agent/
├─ audio/                 # 원본 녹음 (읽기 전용)
├─ transcripts/original/  # 원본 녹취 (읽기 전용)
├─ meetings/              # 회의 분석 결과
├─ actions/               # Action List
├─ docs/
├─ tests/
├─ .claude/
│  ├─ agents/             # meeting-analyst, action-manager, reviewer
│  ├─ skills/             # analyze-meeting, check-actions
│  └─ settings.json
├─ CLAUDE.md
├─ specs/001-meeting-action-agent/   # SPEC.md, tasks.md, plan.md 등
└─ README.md
```

## Agent 역할과 금지사항

| Agent | 역할 | 금지 |
|---|---|---|
| Meeting Analyst | 녹취 읽기, 회의 요약, 결정/미결사항, 지시 후보 추출 | Action 완료 처리, 원본 녹취 수정, 근거 없는 내용 생성 |
| Action Manager | 지시자/담당자/Action/기한/근거/상태 구조화, 기존 Action과 이행 비교 | 완료 확정, 담당자·기한 추정 |
| Reviewer | 결과가 녹취에 근거하는지 검증 후 **PASS/REJECT만** 판정 | 결과 직접 수정 (읽기 전용) |

- Reviewer가 REJECT하면 Action Manager가 재분석한다. **재시도 횟수는 제한**(SPEC 참조)하고, 계속 실패하면 사람에게 넘긴다.
- Reviewer PASS 기준: 담당자·Action 근거 존재, 기한 근거 존재 또는 `미정`, 타임스탬프 존재.
- Reviewer REJECT 기준: 녹취에 없는 담당자/기한 생성, 지시가 아닌 내용을 지시로 판단, 근거 없는 Action.

## Action 상태

| 상태 | 의미 | 설정 주체 |
|---|---|---|
| 🔵 신규 | 이번 회의에서 새로 발생 | Agent |
| 🟠 진행중 | 기한 전, 진행 중 | Agent |
| 🟢 완료 후보 | 완료 근거를 Agent가 발견 | Agent |
| ✅ 완료 | 사람이 최종 승인 | **사람만** |
| 🔴 미완료 | 미완료 근거가 확인됨 | Agent |
| 🟡 확인 필요 | 담당/기한/완료 여부 불명확 | Agent |

`완료`로의 상태 변경은 `approved_by_human` 값이 있을 때만 허용한다. 없으면 차단한다.

## 데이터 인터페이스 (A ↔ B)

녹취는 발언 단위 JSON으로 주고받는다. 필드를 임의로 바꾸지 않는다(변경 시 SPEC.md 먼저 수정).

```json
{
  "meeting_id": "20261110-01",
  "speaker": "팀장",
  "timestamp": "00:12:31",
  "text": "민수 씨 생산성 자료 다시 확인해서 목요일 오전까지 주세요."
}
```

## 개발 규칙

- 언어: Python. 테스트: pytest (`tests/`).
- STT: Whisper 계열(화자 구분은 별도 처리). Dashboard: React 18 + TypeScript + Vite(frontend/) + FastAPI(Backend API, `src/meeting_action_agent/api/`).
- 테스트 데이터는 **가상의 회의**만 사용한다. 실제 회사 회의 데이터를 넣지 않는다.
- 수용기준 테스트: 존재하지 않는 담당자/기한을 생성하면 실패로 처리한다.
- MVP 제외 항목(메일/Teams 알림, 캘린더 등록, 복잡한 권한관리, 대규모 사용자 관리)은 핵심 기능 완성 전에 구현하지 않는다.
- 변경 전 `SPEC.md`와 충돌하는지 확인하고, 충돌하면 사용자에게 먼저 묻는다.

## 응답/작성 언어

사용자 대면 문서·설명은 한국어, 코드 식별자와 기술 용어는 원문 그대로 사용한다.
