import type { SpeakerList } from './api'

/** 화자 하나에 대한 사용자 입력. name이 빈 문자열이거나 unsure면 미확인으로 저장된다. */
export interface SpeakerInput {
  name: string
  unsure: boolean
}
export type SpeakerInputs = Record<string, SpeakerInput>

/** 입력 → 서버 요청 형태. 미확인은 null (서버는 저장하지 않는다). 이름은 사람이 직접 입력한 값만 보낸다. */
export function toSpeakerMap(list: SpeakerList, inputs: SpeakerInputs): Record<string, string | null> {
  const out: Record<string, string | null> = {}
  for (const s of list.speakers) {
    const i = inputs[s.speaker]
    const name = i && !i.unsure ? i.name.trim() : ''
    out[s.speaker] = name || null
  }
  return out
}
