# Meeting Action Agent

> AI가 회의를 요약하는 데서 끝나는 것이 아니라, 여러 Agent가 지시사항과 이행 여부를 각각 분석하고 Reviewer가 검증합니다.
> AI가 확신할 수 없는 화자는 사람이 확인하고, 최종 완료 판단 역시 사람이 승인합니다.

회의 녹음을 받아 **지시사항(Action)을 추출하고, 다음 회의에서 이행 여부를 점검**하는 Multi-Agent 시스템입니다.

## 핵심 원칙

| 원칙 | 의미 |
|---|---|
| 역할 분리 | 지시사항 추출(Meeting Analyst)과 이행 점검(Follow-up Agent)을 서로 다른 Agent가 각자 분석한다. |
| 독립 검증 | 모든 Agent 출력은 Reviewer가 실제 녹취 근거와 대조해 PASS/REJECT로만 판정한다. Reviewer는 수정·보완하지 않는다. |
| 화자는 사람이 확인 | STT가 주는 `speaker_N`을 AI가 사람 이름으로 추측하지 않는다. 사용자가 확인한 회의 단위 매핑만 사용한다. |
| 완료는 사람이 승인 | Agent는 최대 `완료 후보`까지만 제안한다. `완료`는 사람의 승인으로만 전이되며, 승인자와 근거가 기록된다. |
| 원본 불변 | 원본 녹음과 원본 transcript는 수정하지 않는다. 화자 이름은 별도 metadata에 저장한다. |
| 추정 금지 | 담당자·기한을 추정하지 않는다. 담당자 identity가 증명될 때만 완료 후보가 허용된다. |

## 처리 흐름

```
회의 녹음 업로드
  → STT (Gemini)                         원본 transcript (speaker_N, timestamp, 원문)
  → 화자 확인 (사람)                       speaker_N → 이름, 회의 단위 저장
  → Multi-Agent 분석 (병렬)
        ├─ Meeting Analyst  → 요약·결정·미결·신규 지시사항 후보 → Reviewer 검증
        └─ Follow-up Agent  → 이전 회의 Action 이행 여부 판단   → Reviewer 검증
  → Action Manager                        구조화 (담당자·업무·기한 원문/정규화·근거)
  → Human Review (사람)                   🟢 완료 후보 → ✅ 완료 / 계속 진행 / 확인 필요
```

### Action 상태

`🔵 신규` → `🟢 완료 후보`(Agent 제안) → `✅ 완료`(사람 승인).
모든 전이는 `history`에 남고, 완료에는 `approved_by_human`(승인자·시각·근거)이 붙습니다.

### Agent 구성

| Agent | 역할 | 하지 않는 일 |
|---|---|---|
| `meeting-analyst` | 녹취 → 요약·결정·미결·ActionCandidate | Action 직접 생성·저장, 담당자·기한 추정 |
| `action-manager` | 후보를 Action으로 구조화, 이전 Action 이행 점검 | 담당자·기한 추정, 완료 확정 |
| `reviewer` | 후보·판정이 녹취 근거에 존재하는지 PASS/REJECT | 수정·보완·추정·저장 |

## 구조

```
src/meeting_action_agent/   백엔드 (FastAPI, Agent·Orchestrator·Store)
frontend/                   React UI (Vite + TypeScript)
specs/                      SPEC
tests/                      pytest (외부 API 호출 없음)
scripts/                    smoke / TTS fixture 스크립트
```

## 실행

### 환경 변수

| 이름 | 설명 |
|---|---|
| `GEMINI_API_KEY` | `.env`에 설정 (`.env.example` 참고). 코드·로그·결과에 남기지 않는다. |
| `GEMINI_MODEL` | 선택. 사용할 모델명 |
| `MAA_MODE` | `live`(실제 Gemini) / `demo`(기본값, 외부 호출 없음). 요청으로 바꿀 수 없다. |
| `MAA_RUNTIME_DIR` | 선택. 저장 루트 (기본 `runtime/`, 모드별로 분리) |

`live` 모드에서만 Gemini 키가 필요합니다. 지난 회의 브리핑의 음성(TTS)은 Windows 로컬 음성(SAPI, Microsoft Heami ko-KR)을 쓰며 외부 TTS는 쓰지 않습니다. 시연·테스트에는 실제 회의가 아닌 가상(synthetic) 데이터만 사용하세요.

### 백엔드

```bash
pip install fastapi uvicorn python-multipart pydantic google-genai pytest
set PYTHONPATH=src
set MAA_MODE=demo
uvicorn meeting_action_agent.api.app:create_app_from_env --factory --port 8000
```

### 프론트엔드

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

### 테스트

```bash
python -m pytest -q          # 외부 API 호출 없음
cd frontend && npm run build && npm run lint
```

## API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/meetings` | 회의 등록 (제목, 회의일, 녹음 파일) |
| GET | `/api/meetings` | 회의 목록 (이전 회의 선택용) |
| POST | `/api/meetings/{id}/transcribe` | STT만 실행, 화자 목록 반환 |
| PUT | `/api/meetings/{id}/speaker-map` | 사용자가 확인한 화자 이름 저장 |
| POST | `/api/meetings/{id}/analyze` | Multi-Agent 분석 (`previous_meeting_ids`를 명시) |
| GET | `/api/meetings/{id}/result` | 분석 결과 조회 |
| POST | `/api/briefings` | 지난 회의 브리핑 생성 (`previous_meeting_id`, `current_date`). Reviewer 통과 시에만 텍스트+음성 제공 |
| GET | `/api/briefings/{id}/audio` | 저장된 브리핑 WAV 재생 (LLM 호출 없음) |
| POST | `/api/actions/{id}/review` | 사람의 완료·계속 진행·확인 필요 처리 |
| POST | `/api/actions/{id}/withdraw-completion` | 완료 철회 |

## 검증 현황

- pytest 915 PASS, `npm run build` / `npm run lint` PASS
- Backend LIVE E2E 1회 검증 (실제 Gemini STT → 화자 확인 → 병렬 Multi-Agent → 완료 후보 → 사람 승인 → 완료). 가상(synthetic/TTS) 회의만 사용했다.
- 브리핑(생성·Reviewer·TTS·WAV 재생)과 2차 회의 Follow-up은 live API로 검증했다. React 화면의 LIVE 브라우저 수동 E2E는 `DEMO_PREP.md` 체크리스트로 진행한다.
- LLM 출력은 실행마다 달라질 수 있다. Reviewer가 근거 불일치를 거부하면 Action이 생성되지 않을 수 있다(안전 동작).

## 한계

- STT 화자 분리와 timestamp는 Gemini가 정확성을 보장하지 않는다. 그래서 화자는 사람이 확인한다.
- 시리즈(이전 회의) 자동 추론은 없다. 이전 회의는 사용자가 명시한다.
- 오디오는 inline 전송이라 크기 상한이 있다 (Files API는 후속).
