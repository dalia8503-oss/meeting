import assert from 'node:assert/strict'
import { test } from 'node:test'
import { dayLabel, groupByDate } from '../src/minutesGroup.ts'

const m = (id: string, date: string, status: 'analyzed' | 'uploaded' = 'analyzed') =>
  ({ meeting_id: id, title: id, meeting_date: date, created_at: null, analysis_status: status, action_count: 0 })

test('날짜별 그룹: 월 → 날짜 → 회의, 서버가 준 순서를 유지한다', () => {
  const g = groupByDate([m('c', '2026-11-17'), m('b', '2026-11-10'), m('d', '2026-11-10'), m('a', '2026-10-27')])
  assert.deepEqual(g.map((x) => x.month), ['2026-11', '2026-10'])
  assert.deepEqual(g[0].days.map((d) => d.date), ['2026-11-17', '2026-11-10'])
  assert.deepEqual(g[0].days[1].items.map((x) => x.meeting_id), ['b', 'd'])
})

test('회의가 없으면 빈 목록', () => {
  assert.deepEqual(groupByDate([]), [])
})

test('요일 표시와 잘못된 날짜 원문 유지', () => {
  assert.equal(dayLabel('2026-11-10'), '11월 10일 (화)')
  assert.equal(dayLabel('2026-02-30'), '2026-02-30')
  assert.equal(dayLabel('abc'), 'abc')
})
