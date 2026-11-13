import type { ConfidenceLevel, SpeakerList, SpeakerMapValue } from './api'

/**
 * 화자 하나에 대한 사용자 입력.
 * reviewed=false 이면 AI 추천이 입력창에 미리 들어 있을 뿐 사람이 확인하지 않은 상태다 (확정이 아니다).
 */
export interface SpeakerInput {
  name: string
  /** 소속 부/과 (선택). 사람이 입력한 값만 저장되며 표시·향후 조직별 조회용이다 (판단에는 쓰이지 않는다) */
  department?: string
  section?: string
  unsure: boolean
  reviewed: boolean
}
export type SpeakerInputs = Record<string, SpeakerInput>

/** 서버 상태 → 초기 입력. 확정값은 reviewed, AI 추천은 미리 채우되 reviewed=false, 추천이 없으면 빈 입력(미확인). */
export function initialInputs(list: SpeakerList): SpeakerInputs {
  return Object.fromEntries(
    list.speakers.map((s) => {
      if (s.confirmed_name) {
        const p = s.confirmed_profile
        return [s.speaker, { name: s.confirmed_name, department: p?.department ?? '', section: p?.section ?? '', unsure: false, reviewed: true }]
      }
      const sg = s.suggestion
      if (sg?.suggested_name) {
        return [s.speaker, { name: sg.suggested_name, department: sg.department ?? '', section: sg.section ?? '', unsure: false, reviewed: false }]
      }
      return [s.speaker, { name: '', unsure: false, reviewed: true }]
    }),
  )
}

/** 사람이 아직 확인하지 않은 AI 추천이 남아 있는 화자 목록. 비어 있어야 분석을 시작할 수 있다. */
export function pendingSpeakers(list: SpeakerList, inputs: SpeakerInputs): string[] {
  return list.speakers.filter((s) => inputs[s.speaker] && !inputs[s.speaker].reviewed).map((s) => s.speaker)
}

/** 입력 → 서버 요청 형태. 사람이 확인(reviewed)한 이름만 보낸다. 미확인·미검토는 null (서버는 저장하지 않는다). */
export function toSpeakerMap(list: SpeakerList, inputs: SpeakerInputs): Record<string, SpeakerMapValue> {
  const out: Record<string, SpeakerMapValue> = {}
  for (const s of list.speakers) {
    const i = inputs[s.speaker]
    const name = i && i.reviewed && !i.unsure ? i.name.trim() : ''
    out[s.speaker] = name ? { name, department: i.department?.trim() || null, section: i.section?.trim() || null } : null
  }
  return out
}

/** 최종 표시명: "1부 3과 이과장" (부/과가 없으면 이름만). */
export function displayName(i: Pick<SpeakerInput, 'name' | 'department' | 'section'>): string {
  return [i.department, i.section, i.name].map((x) => x?.trim()).filter(Boolean).join(' ')
}

export const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = { high: '높음', medium: '보통', low: '낮음' }
