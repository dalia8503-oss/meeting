import type { MeetingSummary } from './api'

export type MeetingsLoad =
  | { s: 'loading' }
  | { s: 'error' }
  | { s: 'ok'; meetings: MeetingSummary[] }

interface Props {
  load: MeetingsLoad
  selected: string[]
  onChange: (ids: string[]) => void
  disabled: boolean
}

/** 이전 회의는 서버 목록(GET /api/meetings)에서 사용자가 명시적으로 고른다 (자동 추론 없음). 선택이 없으면 API에는 [] 가 전달된다. */
export default function PreviousMeetings({ load, selected, onChange, disabled }: Props) {
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  const meetings = load.s === 'ok' ? load.meetings : []
  return (
    <section className="panel prev-panel" aria-label="이전 회의 이행점검">
      <h2 className="panel-title">이전 회의 이행점검</h2>
      <p className="muted small">이전 회의에서 지시한 일이 이번 회의에서 이행됐는지 함께 점검합니다.</p>
      {load.s === 'loading' && <p className="muted small">이전 회의 불러오는 중…</p>}
      {load.s === 'error' && <p className="error small" role="alert">이전 회의 목록을 불러오지 못했습니다.</p>}
      <ul className="prev-list">
        <li>
          <label className="prev-item">
            <input type="checkbox" checked={selected.length === 0} disabled={disabled} onChange={() => onChange([])} />
            <span>이전 회의 없음</span>
          </label>
        </li>
        {meetings.map((m) => {
          // 분석되지 않았거나 Action이 없는 회의는 이행점검할 대상이 없다.
          const usable = m.analysis_status === 'analyzed' && m.action_count > 0
          return (
            <li key={m.meeting_id}>
              <label className="prev-item" style={usable ? undefined : { opacity: 0.55, cursor: 'not-allowed' }}>
                <input
                  type="checkbox"
                  checked={selected.includes(m.meeting_id)}
                  disabled={disabled || !usable}
                  onChange={() => toggle(m.meeting_id)}
                />
                <span>
                  {m.title} <span className="muted small">{m.meeting_date}</span>{' '}
                  <span className="muted small">
                    {usable ? `Action ${m.action_count}건` : m.analysis_status === 'analyzed' ? 'Action 없음' : '분석 전'}
                  </span>
                </span>
              </label>
            </li>
          )
        })}
      </ul>
      {load.s === 'ok' && meetings.length === 0 && <p className="muted small">이행점검할 이전 회의가 없습니다.</p>}
    </section>
  )
}
