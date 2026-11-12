import { Fragment, useState } from 'react'
import type { ActionView, AnalysisResult, SummaryItem } from './api'
import { CollabPanel, FollowupPanel, ApprovalInfo } from './FollowupView'
import { StatusBadge } from './StatusBadge'

const STEPS = ['음성 업로드', 'STT', '회의 분석', 'Reviewer', 'Action 생성']

export type Phase = 'idle' | 'uploaded' | 'analyzing' | 'done'

/** 서버가 단계별 이벤트를 주지 않으므로 분석 중에는 2~5단계를 함께 "진행 중"으로만 표시한다. */
export function Progress({ phase }: { phase: Phase }) {
  const mark = (i: number) => {
    if (phase === 'done') return { c: 'done', m: '✓' }
    if (phase === 'idle') return { c: 'wait', m: '○' }
    if (i === 0) return { c: 'done', m: '✓' }
    return phase === 'analyzing' ? { c: 'run', m: '●' } : { c: 'wait', m: '○' }
  }
  return (
    <section className="panel progress" aria-label="분석 진행 상태">
      <h2 className="panel-title">분석 진행 상태</h2>
      <ol className="steps">
        {STEPS.map((s, i) => {
          const { c, m } = mark(i)
          return (
            <li key={s} className={`step step-${c}`}>
              <span className="step-mark" aria-hidden="true">{m}</span> {s}
            </li>
          )
        })}
      </ol>
      {phase === 'analyzing' && <p className="muted small">AI Agent들이 회의를 분석하고 있습니다. 잠시만 기다려 주세요...</p>}
    </section>
  )
}

const DAYS = ['일', '월', '화', '수', '목', '금', '토']

/** YYYY-MM-DD → MM/DD(요일). 형식이 다르면 null (원문만 표시). */
function shortDate(iso: string | null): string | null {
  const m = iso?.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]))
  if (d.getUTCMonth() !== +m[2] - 1) return null
  return `${m[2]}/${m[3]}(${DAYS[d.getUTCDay()]})`
}

function Due({ a }: { a: ActionView }) {
  const norm = shortDate(a.due_date_normalized)
  if (!a.due_date && !norm) return <span className="muted">미지정</span>
  return (
    <>
      {a.due_date && <div>{a.due_date}</div>}
      {norm && <div className="muted small">{norm}</div>}
    </>
  )
}

function ActionRow({ a, meetingId }: { a: ActionView; meetingId: string }) {
  const [open, setOpen] = useState(false)
  const panelId = `ev-${a.action_id}`
  return (
    <Fragment>
      <tr>
        <td>
          <StatusBadge status={a.status} />
          <ApprovalInfo a={a} />
        </td>
        <td className="c-assignee">{a.assignee || <span className="muted">미지정</span>}</td>
        <td className="c-task">
          {a.task}
          <div className={`origin ${a.meeting_id === meetingId ? 'origin-new' : 'origin-prev'}`}>
            {a.action_id} · {a.meeting_id === meetingId ? '이번 회의 신규' : '이전 회의 이행점검'}
          </div>
          {a.flags.map((f) => (
            <span key={f} className="flag">{f}</span>
          ))}
        </td>
        <td className="c-due"><Due a={a} /></td>
        <td className="c-ev">
          <button type="button" className="btn-link" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
            {open ? '근거 닫기' : '근거 보기'}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="ev-row">
          <td colSpan={5} id={panelId}>
            <div className="evidence">
              <div className="ev-meta">
                🎙 {a.evidence.timestamp}
                {a.evidence.speaker && <span className="ev-speaker">{a.evidence.speaker}</span>}
              </div>
              <blockquote>“{a.evidence.text}”</blockquote>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

function ActionList({ actions, meetingId }: { actions: ActionView[]; meetingId: string }) {
  return (
    <section className="panel action-panel" aria-label="Action List">
      <div className="panel-head">
        <h2 className="panel-title big">ACTION LIST</h2>
        <span className="count">총 {actions.length}건</span>
      </div>
      {actions.length === 0 ? (
        <p className="empty">이번 회의에서 등록할 Action이 없습니다.</p>
      ) : (
        <div className="table-wrap">
          <table className="action-table">
            <thead>
              <tr>
                <th>상태</th>
                <th>담당자</th>
                <th>할 일</th>
                <th>기한</th>
                <th>근거</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => (
                <ActionRow key={a.action_id} a={a} meetingId={meetingId} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Timestamps({ ts }: { ts: string[] }) {
  if (ts.length === 0) return null
  return <span className="ts">🎙 {ts.join(' · ')}</span>
}

function ItemList({ items, mark }: { items: SummaryItem[]; mark: string }) {
  return (
    <ul className="item-list">
      {items.map((it, i) => (
        <li key={i} className="item">
          <span className="item-mark" aria-hidden="true">{mark}</span>
          <div>
            <div className="item-text">{it.text}</div>
            <Timestamps ts={it.evidence_timestamps} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function ResultHeader({ result }: { result: AnalysisResult }) {
  return (
    <div className="result-head">
      <strong>✅ 분석 완료</strong>
      <span className="muted">
        Action {result.actions.length}건 · 확인 필요 {result.needs_confirmation.length}건
      </span>
    </div>
  )
}

function NeedsConfirmation({ items }: { items: SummaryItem[] }) {
  if (items.length === 0) return <p className="muted small none-note">확인 필요 없음</p>
  return (
    <section className="panel confirm-panel" aria-label="확인 필요">
      <h3 className="panel-title">
        🟡 확인 필요 <span className="count-sm">{items.length}건</span>
      </h3>
      <ul className="confirm-list">
        {items.map((it, i) => (
          <li key={i} className="confirm-item">
            <div className="item-text">{it.text}</div>
            <Timestamps ts={it.evidence_timestamps} />
          </li>
        ))}
      </ul>
    </section>
  )
}

function Box({ cls, title, sub, items, mark, empty }: {
  cls: string; title: string; sub: string; items: SummaryItem[]; mark: string; empty: string
}) {
  return (
    <section className={`panel box box-${cls}`} aria-label={title}>
      <h3 className="panel-title">{title} <span className="count-sm">{items.length}건</span></h3>
      <p className="muted small box-sub">{sub}</p>
      {items.length === 0 ? <p className="muted small">{empty}</p> : <ItemList items={items} mark={mark} />}
    </section>
  )
}

function Summary({ items }: { items: SummaryItem[] }) {
  return (
    <section className="panel" aria-label="회의 요약">
      <h3 className="panel-title">📝 회의 요약</h3>
      {items.length === 0 ? (
        <p className="muted small">생성된 회의 요약이 없습니다.</p>
      ) : (
        <ItemList items={items} mark="•" />
      )}
    </section>
  )
}

function AiVerification({ rejected }: { rejected: AnalysisResult['rejected'] }) {
  return (
    <details className="panel ai-info">
      <summary>
        AI 검증 정보 <span className="muted small">· Reviewer에서 제외된 항목 {rejected.length}건</span>
      </summary>
      {rejected.length === 0 ? (
        <p className="muted small">제외된 항목이 없습니다.</p>
      ) : (
        <>
          <p className="muted small">근거가 확인되지 않아 Action으로 등록하지 않은 후보입니다.</p>
          <ul className="items">
            {rejected.map((r, i) => (
              <li key={i}>
                {r.task} <span className="muted tiny">[{r.timestamp}] {r.assignee}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  )
}

export default function AnalysisView({ result, onActionUpdated }: { result: AnalysisResult; onActionUpdated: (a: ActionView) => void }) {
  return (
    <>
      <ResultHeader result={result} />
      <CollabPanel result={result} />
      <FollowupPanel result={result} onUpdated={onActionUpdated} />
      {result.new_actions_error && (
        <p className="error" role="alert">신규 Action 추출 중 문제가 발생했습니다. 이전 지시 이행점검 결과는 유지되었습니다.</p>
      )}
      <ActionList actions={result.actions} meetingId={result.meeting_id} />
      <NeedsConfirmation items={result.needs_confirmation} />
      <div className="two-col">
        <Box cls="decision" title="✓ 결정사항" sub="회의에서 확정된 내용" items={result.decisions} mark="✓" empty="확정된 결정사항이 없습니다." />
        <Box cls="open" title="◌ 미결사항" sub="추가 논의 또는 확인이 필요한 내용" items={result.open_issues} mark="◌" empty="남은 미결사항이 없습니다." />
      </div>
      <Summary items={result.summary} />
      <AiVerification rejected={result.rejected} />
    </>
  )
}
