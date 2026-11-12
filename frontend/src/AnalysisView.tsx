import { Fragment, useState } from 'react'
import type { ActionView, AnalysisResult, SummaryItem } from './api'

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
      {phase === 'analyzing' && <p className="muted small">회의를 분석하고 있습니다. 잠시만 기다려 주세요...</p>}
    </section>
  )
}

const STATUS: Record<string, { icon: string; cls: string }> = {
  '신규': { icon: '🔵', cls: 'new' },
  '진행중': { icon: '🟠', cls: 'doing' },
  '완료 후보': { icon: '🟢', cls: 'cand' },
  '완료': { icon: '✅', cls: 'done' },
  '미완료': { icon: '🔴', cls: 'fail' },
  '확인 필요': { icon: '🟡', cls: 'confirm' },
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { icon: '⚪', cls: 'other' }
  return (
    <span className={`status status-${s.cls}`}>
      <span aria-hidden="true">{s.icon}</span> {status}
    </span>
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

function ActionRow({ a }: { a: ActionView }) {
  const [open, setOpen] = useState(false)
  const panelId = `ev-${a.action_id}`
  return (
    <Fragment>
      <tr>
        <td><StatusBadge status={a.status} /></td>
        <td className="c-assignee">{a.assignee || <span className="muted">미지정</span>}</td>
        <td className="c-task">
          {a.task}
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

function ActionList({ actions }: { actions: ActionView[] }) {
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
                <ActionRow key={a.action_id} a={a} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Items({ items }: { items: SummaryItem[] }) {
  return (
    <ul className="items">
      {items.map((it, i) => (
        <li key={i}>
          {it.text} <span className="muted tiny">[{it.evidence_timestamps.join(', ')}]</span>
        </li>
      ))}
    </ul>
  )
}

function Section({ title, items }: { title: string; items: SummaryItem[] }) {
  if (items.length === 0) return null
  return (
    <section className="panel">
      <h3 className="panel-title">{title}</h3>
      <Items items={items} />
    </section>
  )
}

function Rejected({ rejected }: { rejected: AnalysisResult['rejected'] }) {
  if (rejected.length === 0) return null
  return (
    <details className="panel rejected">
      <summary>Reviewer 검증에서 제외된 항목 {rejected.length}건</summary>
      <p className="muted small">근거가 확인되지 않아 Action으로 등록하지 않은 후보입니다.</p>
      <ul className="items">
        {rejected.map((r, i) => (
          <li key={i}>
            {r.task} <span className="muted tiny">[{r.timestamp}] {r.assignee}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}

export default function AnalysisView({ result }: { result: AnalysisResult }) {
  const confirm = result.needs_confirmation
  return (
    <>
      <ActionList actions={result.actions} />
      {confirm.length > 0 && (
        <section className="panel confirm-panel">
          <h3 className="panel-title">🟡 확인 필요 <span className="count-sm">{confirm.length}건</span></h3>
          <Items items={confirm} />
        </section>
      )}
      <Rejected rejected={result.rejected} />
      <div className="secondary">
        <Section title="결정사항" items={result.decisions} />
        <Section title="미결사항" items={result.open_issues} />
        <Section title="회의 요약" items={result.summary} />
      </div>
    </>
  )
}
