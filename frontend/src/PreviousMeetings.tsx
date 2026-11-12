import { useState } from 'react'
import { deleteMeeting, type MeetingSummary } from './api'

export type MeetingsLoad =
  | { s: 'loading' }
  | { s: 'error' }
  | { s: 'ok'; meetings: MeetingSummary[] }

interface Props {
  load: MeetingsLoad
  selected: string[]
  onChange: (ids: string[]) => void
  /** 서버가 삭제 성공을 돌려준 뒤에만 호출된다 (선택 정리·목록 refresh는 부모 담당). */
  onDeleted: (id: string) => void
  disabled: boolean
}

/** 이전 회의는 서버 목록(GET /api/meetings)에서 사용자가 명시적으로 고른다 (자동 추론 없음). 선택이 없으면 API에는 [] 가 전달된다. */
export default function PreviousMeetings({ load, selected, onChange, onDeleted, disabled }: Props) {
  const [deleting, setDeleting] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  const meetings = load.s === 'ok' ? load.meetings : []

  /** 삭제는 사용자가 버튼을 눌러 확인한 경우에만 실행한다. 실패하면 목록에서 임의로 제거하지 않는다. */
  const remove = async (m: MeetingSummary) => {
    if (deleting) return
    if (!window.confirm(`"${m.title}" 회의를 삭제하시겠습니까?\n\n회의 기록과 관련 분석 결과가 삭제됩니다.`)) return
    setDeleting(m.meeting_id)
    setDeleteError(null)
    try {
      await deleteMeeting(m.meeting_id)
      onDeleted(m.meeting_id)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : '회의를 삭제하지 못했습니다.')
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section className="panel prev-panel" aria-label="지난 회의 선택">
      <h2 className="panel-title">지난 회의 선택</h2>
      <p className="muted small">
        지난 회의를 선택하면 그 회의에서 지시한 일이 이번 회의에서 이행됐는지 함께 점검합니다. 잘못 등록한 회의는 삭제할 수 있습니다.
      </p>
      {load.s === 'loading' && <p className="muted small">지난 회의 불러오는 중…</p>}
      {load.s === 'error' && <p className="error small" role="alert">지난 회의 목록을 불러오지 못했습니다.</p>}
      {deleteError && <p className="error small" role="alert">{deleteError}</p>}
      <ul className="prev-list">
        <li>
          <label className="prev-item">
            <input type="checkbox" checked={selected.length === 0} disabled={disabled} onChange={() => onChange([])} />
            <span>이전 회의 없음</span>
          </label>
        </li>
        {meetings.map((m) => {
          // 분석되지 않았거나 Action이 없는 회의는 이행점검할 대상이 없다 (선택 불가, 삭제는 가능).
          const usable = m.analysis_status === 'analyzed' && m.action_count > 0
          return (
            <li key={m.meeting_id} className="prev-row">
              <label className="prev-item" style={usable ? undefined : { opacity: 0.55, cursor: 'not-allowed' }}>
                <input
                  type="checkbox"
                  checked={selected.includes(m.meeting_id)}
                  disabled={disabled || !usable}
                  onChange={() => toggle(m.meeting_id)}
                />
                <span>
                  <span className="muted small">{m.meeting_date.replaceAll('-', '.')}</span> <strong>{m.title}</strong>{' '}
                  <span className="muted small">
                    {m.analysis_status === 'analyzed' ? `분석 완료 · Action ${m.action_count}건` : '미분석'}
                  </span>
                </span>
              </label>
              <button
                type="button"
                className="btn-link prev-delete"
                disabled={disabled || deleting !== null}
                onClick={() => remove(m)}
                aria-label={`${m.title} 삭제`}
              >
                {deleting === m.meeting_id ? '삭제 중…' : '🗑 삭제'}
              </button>
            </li>
          )
        })}
      </ul>
      {load.s === 'ok' && meetings.length === 0 && <p className="muted small">등록된 지난 회의가 없습니다.</p>}
    </section>
  )
}
