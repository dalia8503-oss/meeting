import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchMeetings, fetchResult, type AnalysisResult, type MeetingSummary } from './api'
import { ActionList, Box, NeedsConfirmation, Summary } from './AnalysisView'
import { dayLabel, groupByDate } from './minutesGroup'

type Load = { s: 'loading' } | { s: 'error' } | { s: 'ok'; meetings: MeetingSummary[] }
type Detail =
  | { s: 'idle' }
  | { s: 'loading' }
  | { s: 'error'; message: string }
  | { s: 'ok'; result: AnalysisResult }

/**
 * 회의록: 저장된 회의를 날짜별로 정리해 보고, 선택한 회의의 분석 결과를 읽기 전용으로 보여 준다.
 * 이 화면은 분석·확정·승인·화자 수정을 하지 않는다 (조회 전용). 데이터는 GET /api/meetings, GET /api/meetings/{id}/result.
 */
export default function MinutesView({ active }: { active: boolean }) {
  const [load, setLoad] = useState<Load>({ s: 'loading' })
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<Detail>({ s: 'idle' })
  const seq = useRef(0)

  const refresh = useCallback(() => {
    fetchMeetings()
      .then((meetings) => setLoad({ s: 'ok', meetings }))
      .catch(() => setLoad({ s: 'error' }))
  }, [])

  // 탭을 열 때마다 최신 목록을 읽는다 (다른 탭에서 등록·분석·삭제한 회의 반영). 기존 목록은 읽는 동안 그대로 둔다.
  useEffect(() => {
    if (active) refresh()
  }, [active, refresh])

  const open = (id: string) => {
    setSelected(id)
    setDetail({ s: 'loading' })
    const mine = ++seq.current // 늦게 도착한 이전 응답이 현재 선택을 덮어쓰지 않게 한다
    fetchResult(id)
      .then((result) => seq.current === mine && setDetail({ s: 'ok', result }))
      .catch((err) => seq.current === mine && setDetail({ s: 'error', message: err instanceof Error ? err.message : '회의록을 불러오지 못했습니다.' }))
  }

  const meetings = load.s === 'ok' ? load.meetings : []
  const groups = groupByDate(meetings)
  const stillThere = selected !== null && meetings.some((m) => m.meeting_id === selected)

  return (
    <div className="minutes">
      <section className="panel minutes-list" aria-label="회의록 목록">
        <div className="panel-head">
          <h2 className="panel-title">회의록</h2>
          <button type="button" className="btn-link small" onClick={() => { setLoad({ s: 'loading' }); refresh() }}>새로고침</button>
        </div>
        {load.s === 'loading' && <p className="muted small">회의 목록 불러오는 중…</p>}
        {load.s === 'error' && <p className="error small" role="alert">회의 목록을 불러오지 못했습니다.</p>}
        {load.s === 'ok' && meetings.length === 0 && <p className="muted small">저장된 회의가 없습니다.</p>}
        {groups.map((g, gi) => (
          <details key={g.month} className="minutes-month" open={gi === 0}>
            <summary>
              <strong>{g.month.replace('-', '년 ')}월</strong>{' '}
              <span className="muted small">{g.days.reduce((n, d) => n + d.items.length, 0)}건</span>
            </summary>
            {g.days.map((d) => (
              <div key={d.date} className="minutes-day">
                <div className="minutes-date">{dayLabel(d.date)}</div>
                <ul className="minutes-items">
                  {d.items.map((m) => {
                    const analyzed = m.analysis_status === 'analyzed'
                    return (
                      <li key={m.meeting_id}>
                        <button
                          type="button"
                          className={`minutes-item${m.meeting_id === selected ? ' minutes-item-on' : ''}`}
                          disabled={!analyzed}
                          aria-current={m.meeting_id === selected ? 'true' : undefined}
                          onClick={() => open(m.meeting_id)}
                        >
                          <strong>{m.title}</strong>
                          <span className="muted small">{analyzed ? `Action ${m.action_count}건` : '미분석'}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </details>
        ))}
      </section>

      <div className="minutes-detail">
        {(!selected || (load.s === 'ok' && !stillThere)) && (
          <p className="empty">왼쪽에서 회의를 선택하면 회의록이 표시됩니다.</p>
        )}
        {selected && stillThere && detail.s === 'loading' && <p className="muted">회의록 불러오는 중…</p>}
        {selected && stillThere && detail.s === 'error' && <p className="error" role="alert">{detail.message}</p>}
        {selected && stillThere && detail.s === 'ok' && <MinutesDetail result={detail.result} meeting={meetings.find((m) => m.meeting_id === selected)} />}
      </div>
    </div>
  )
}

function MinutesDetail({ result, meeting }: { result: AnalysisResult; meeting?: MeetingSummary }) {
  return (
    <>
      <section className="panel minutes-head">
        <h2 className="panel-title big">{meeting?.title ?? result.meeting_id}</h2>
        <div className="muted small">
          {meeting ? dayLabel(meeting.meeting_date) : ''} · Action {result.actions.length}건 · 확인 필요 {result.needs_confirmation.length}건
          {result.analysis_finalized && <span className="flag">확정됨</span>}
        </div>
        {result.stale && (
          <p className="small next-stale" role="status">
            화자 정보가 변경되어 이 분석은 최신이 아닙니다. 회의 분석 탭에서 재분석할 수 있습니다.
          </p>
        )}
      </section>
      <Summary items={result.summary} />
      <div className="two-col">
        <Box cls="decision" title="✓ 결정사항" sub="회의에서 확정된 내용" items={result.decisions} mark="✓" empty="확정된 결정사항이 없습니다." />
        <Box cls="open" title="◌ 미결사항" sub="추가 논의 또는 확인이 필요한 내용" items={result.open_issues} mark="◌" empty="남은 미결사항이 없습니다." />
      </div>
      <NeedsConfirmation items={result.needs_confirmation} />
      <ActionList actions={result.actions} meetingId={result.meeting_id} rejectedCount={0} showNoActionNote={false} />
      <details className="panel transcript-panel">
        <summary>녹취 전문 <span className="muted small">· {result.transcript.length}개 발화 (읽기 전용)</span></summary>
        <ul className="transcript-list">
          {result.transcript.map((t, i) => (
            <li key={`${t.timestamp}-${i}`} className="transcript-line">
              <span className="muted small">{t.timestamp}</span>{' '}
              <strong>{t.speaker_display ?? t.speaker_name ?? t.speaker}</strong>
              <div className="transcript-text">“{t.text}”</div>
            </li>
          ))}
        </ul>
      </details>
    </>
  )
}
