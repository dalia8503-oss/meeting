// 서버에 회의 목록 API가 없으므로, 이 브라우저에서 분석을 끝낸 회의만 "이전 회의 후보"로 기억한다.
export interface KnownMeeting {
  meeting_id: string
  title: string
  meeting_date: string
}

const KEY = 'maa.analyzedMeetings'

export function loadKnownMeetings(): KnownMeeting[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    if (!Array.isArray(v)) return []
    return v.filter(
      (m): m is KnownMeeting =>
        !!m && typeof m.meeting_id === 'string' && typeof m.title === 'string' && typeof m.meeting_date === 'string',
    )
  } catch {
    return []
  }
}

export function rememberMeeting(m: KnownMeeting): KnownMeeting[] {
  const list = [m, ...loadKnownMeetings().filter((x) => x.meeting_id !== m.meeting_id)].slice(0, 30)
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* 저장 실패해도 화면 동작에는 영향 없음 */
  }
  return list
}
