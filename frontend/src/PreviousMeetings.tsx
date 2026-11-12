import type { KnownMeeting } from './knownMeetings'

interface Props {
  meetings: KnownMeeting[]
  selected: string[]
  onChange: (ids: string[]) => void
  disabled: boolean
}

/** 이전 회의는 사용자가 명시적으로 고른다 (자동 추론 없음). 선택이 없으면 API에는 [] 가 전달된다. */
export default function PreviousMeetings({ meetings, selected, onChange, disabled }: Props) {
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  return (
    <section className="panel prev-panel" aria-label="이전 회의 이행점검">
      <h2 className="panel-title">이전 회의 이행점검</h2>
      <p className="muted small">이전 회의에서 지시한 일이 이번 회의에서 이행됐는지 함께 점검합니다.</p>
      <ul className="prev-list">
        <li>
          <label className="prev-item">
            <input type="checkbox" checked={selected.length === 0} disabled={disabled} onChange={() => onChange([])} />
            <span>이전 회의 없음</span>
          </label>
        </li>
        {meetings.map((m) => (
          <li key={m.meeting_id}>
            <label className="prev-item">
              <input type="checkbox" checked={selected.includes(m.meeting_id)} disabled={disabled} onChange={() => toggle(m.meeting_id)} />
              <span>
                {m.title} <span className="muted small">{m.meeting_date}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      {meetings.length === 0 && <p className="muted small">이 브라우저에서 분석을 마친 이전 회의가 아직 없습니다.</p>}
    </section>
  )
}
