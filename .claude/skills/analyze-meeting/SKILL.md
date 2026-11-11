---
name: analyze-meeting
description: 회의 녹취(Transcript)를 분석해 요약·결정·지시·미결·확인 필요를 보고하고 Action을 저장한다. 기존 Meeting Analyst → 검증 → Action Manager 파이프라인을 호출하는 얇은 진입점.
---

# /analyze-meeting

이 Skill은 **규칙을 구현하지 않는다.** 분류·담당자·기한·status·중복 규칙은 모두 기존 코드와 에이전트 정의를 따른다.

## 절차

1. **입력 확인** — `transcripts/original/<meeting_id>.json`(Utterance 배열)과 회의 metadata(`meeting_date`, `participants`). 원본 transcript는 수정하지 않는다.
2. **분석** — `meeting-analyst` 에이전트를 호출해 `MeetingAnalysis` JSON을 받는다 (`.claude/agents/meeting-analyst.md`). 에이전트가 Action을 직접 만들거나 저장하게 하지 않는다.
3. **검증·저장·변환** — 분석 JSON을 파일로 두고 실행한다.
   ```
   python -m meeting_action_agent.analyze_meeting <transcript.json> <analysis.json> <metadata.json> <actions.json> [meetings_dir]
   ```
   내부 흐름: `validate_analysis` → `save_analysis` → `pipeline.extract_and_convert` → Action Manager(`convert_candidate`).
   검증 실패 시 예외로 중단되며 아무것도 저장하지 않는다. 오류를 임의로 고치거나 값을 보완하지 않는다.
4. **보고** — 출력 순서: 요약 → 결정 → 지시(생성된 Action) → 미결 → 확인 필요. 지시 0건은 오류가 아니며 Action 0건 + 요약만 보고한다.

## 금지

- 담당자·기한·환산 날짜를 직접 채우거나 수정하지 않는다.
- `완료` 상태·`approved_by_human`·`history`를 설정하지 않는다.
- 제안·질문을 Action으로 만들지 않는다 (SPEC 5.5, CHK021 결정).
