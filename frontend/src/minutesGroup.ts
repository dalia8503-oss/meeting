import type { MeetingSummary } from './api'

const DAYS = ['일', '월', '화', '수', '목', '금', '토']

/** YYYY-MM-DD → "11월 10일 (화)". 형식이 다르면 원문. */
export function dayLabel(iso: string): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return iso
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]))
  return d.getUTCMonth() === +m[2] - 1 ? `${+m[2]}월 ${+m[3]}일 (${DAYS[d.getUTCDay()]})` : iso
}

/** 월 → 날짜 → 회의 (서버가 이미 최신순으로 정렬해 준다). 날짜를 추정하지 않고 저장된 meeting_date만 쓴다. */
export function groupByDate(meetings: MeetingSummary[]): { month: string; days: { date: string; items: MeetingSummary[] }[] }[] {
  const months = new Map<string, Map<string, MeetingSummary[]>>()
  for (const m of meetings) {
    const month = m.meeting_date.slice(0, 7)
    const days = months.get(month) ?? new Map<string, MeetingSummary[]>()
    days.set(m.meeting_date, [...(days.get(m.meeting_date) ?? []), m])
    months.set(month, days)
  }
  return [...months].map(([month, days]) => ({ month, days: [...days].map(([date, items]) => ({ date, items })) }))
}
