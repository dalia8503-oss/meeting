import { useState } from 'react'
import { deleteMeeting, MeetingReferencedError, type MeetingSummary } from './api'

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
  /** 방금 확정한 회의. 강조 표시만 하며 자동으로 선택하지 않는다 (이전 회의는 사용자가 명시적으로 고른다). */
  highlightId?: string | null
}

/** 이전 회의는 서버 목록(GET /api/meetings)에서 사용자가 명시적으로 고른다 (자동 추론 없음). 선택이 없으면 API에는 [] 가 전달된다. */
export default function PreviousMeetings({ load, selected, onChange, onDeleted, disabled, highlightId }: Props) {
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
      let also: string[] = []
      try {
        also = await deleteMeeting(m.meeting_id)
      } catch (err) {
        if (!(err instanceof MeetingReferencedError)) throw err
        // 다른 회의와 서로 연결돼 있다: 함께 삭제될 회의를 보여 주고, 사용자가 동의한 경우에만 연결 회의까지 삭제한다.
        const list = err.related
          .map((r) => `- ${r.meeting_date.replaceAll('-', '.')} ${r.title} (Action ${r.action_count}건)`)
          .join('\n')
        const ok = window.confirm(
          `"${m.title}" 회의는 아래 회의와 서로 연결되어(이전 회의 선택·이행점검 기록) 단독으로 삭제할 수 없습니다.\n\n` +
            `함께 삭제될 회의 ${err.related.length}건:\n${list}\n\n` +
            '연결된 회의의 분석 결과·Action·이행점검 기록이 모두 삭제되며 되돌릴 수 없습니다. 계속할까요?',
        )
        if (!ok) return
        also = await deleteMeeting(m.meeting_id, true)
      }
      onDeleted(m.meeting_id)
      also.forEach(onDeleted)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : '회의를 삭제하지 못했습니다.')
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section id="prev-meetings" className="panel prev-panel" aria-label="지난 회의 선택">
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
          // 분석된 회의는 선택할 수 있다 (Action 0건이어도 지난 회의 맥락·브리핑에 쓸 수 있다). 미분석 회의만 선택 불가, 삭제는 가능.
          const usable = m.analysis_status === 'analyzed'
          return (
            <li key={m.meeting_id} className={`prev-row${m.meeting_id === highlightId ? ' prev-highlight' : ''}`}>
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
                    {m.analysis_status === 'analyzed' ? `분석 완료 · Action ${m.action_count}건${m.action_count === 0 ? ' (이행점검할 Action 없음)' : ''}` : '미분석'}
                  </span>
                  {m.meeting_id === highlightId && (
                    <span className="prev-tag">
                      방금 확정한 회의{' · 이전 회의로 선택하려면 체크하세요'}
                    </span>
                  )}
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
