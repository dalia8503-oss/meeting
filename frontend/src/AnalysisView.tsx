import { Fragment, useState } from 'react'
import type { ActionView, AnalysisResult, SpeakerList, SummaryItem } from './api'
import { CollabPanel, FollowupPanel, ApprovalInfo } from './FollowupView'
import { StatusBadge } from './StatusBadge'
import { StaleBanner, TranscriptPanel } from './TranscriptView'
import { RejectedList, type AdoptInput } from './AdoptRejected'

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
        <td className="c-assignee">{a.assignee ? (a.assignee_display ?? a.assignee) : <span className="muted">미지정</span>}
          {a.assigner && <div className="muted tiny">지시: {a.assigner_display ?? a.assigner}</div>}</td>
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
                {a.evidence.speaker && (
                  <span className="ev-speaker">
                    {a.evidence.speaker}
                    {a.evidence.speaker_display ?? a.evidence.speaker_name ? ` · ${a.evidence.speaker_display ?? a.evidence.speaker_name}` : ''}
                  </span>
                )}
              </div>
              <blockquote>“{a.evidence.text}”</blockquote>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

/** 접힌 <details>를 열고 그 위치로 이동한다 (사용자가 직접 열고 닫는 기존 동작은 그대로 둔다). */
function openDetails(id: string) {
  const el = document.getElementById(id) as HTMLDetailsElement | null
  if (!el) return
  el.open = true
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function ActionList({ actions, meetingId, rejectedCount, showNoActionNote }: {
  actions: ActionView[]; meetingId: string; rejectedCount: number; showNoActionNote: boolean
}) {
  return (
    <section className="panel action-panel" aria-label="Action List">
      <div className="panel-head">
        <h2 className="panel-title big">ACTION LIST</h2>
        <span className="count">총 {actions.length}건</span>
      </div>
      {actions.length === 0 ? (
        <div className="empty">
          <p className="empty-main">이번 회의에서 등록할 Action이 없습니다.</p>
          {showNoActionNote && <p className="small">Reviewer 검증 결과 Action으로 확정된 항목이 없습니다.</p>}
          {rejectedCount > 0 && (
            <button type="button" className="btn-link small" onClick={() => openDetails('ai-verification')}>
              제외된 후보 {rejectedCount}건 보기
            </button>
          )}
        </div>
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

export function NeedsConfirmation({ items }: { items: SummaryItem[] }) {
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

export function Box({ cls, title, sub, items, mark, empty }: {
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

export function Summary({ items }: { items: SummaryItem[] }) {
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

function AiVerification({ rejected, onAdopt, disabled }: {
  rejected: AnalysisResult['rejected']
  onAdopt: (index: number, input: AdoptInput) => Promise<void>
  disabled: boolean
}) {
  return (
    <details id="ai-verification" className="panel ai-info">
      <summary>
        AI 검증 정보 <span className="muted small">· Reviewer에서 제외된 항목 {rejected.length}건</span>
      </summary>
      {rejected.length === 0 ? (
        <p className="muted small">제외된 항목이 없습니다.</p>
      ) : (
        <>
          <p className="muted small">
            근거가 확인되지 않아 Action으로 등록하지 않은 후보입니다. 사람이 근거를 확인했다면 직접 Action으로 채택할 수 있습니다.
          </p>
          <RejectedList rejected={rejected} onAdopt={onAdopt} disabled={disabled} />
        </>
      )}
    </details>
  )
}

interface NextStepsProps {
  result: AnalysisResult
  onFinalize: () => void
  finalizing: boolean
  finalizeError?: string
  onReanalyze: () => void
  onRerun: () => void
  reanalyzing: boolean
  reanalyzeError?: string
  onNextMeeting: () => void
  onMeetingList: () => void
}

/**
 * 분석 완료 후 "다음 단계". Action 0건이어도 확정할 수 있다 (Action 0건은 오류가 아니다).
 * 확정은 사람이 결과를 확인했다는 표시일 뿐이며 Reviewer 판정·Action 상태를 바꾸지 않는다.
 */
function NextSteps({ result, onFinalize, finalizing, finalizeError, onReanalyze, onRerun, reanalyzing, reanalyzeError, onNextMeeting, onMeetingList }: NextStepsProps) {
  const [editing, setEditing] = useState(false)
  const rejected = result.rejected.length
  const finalized = !!result.analysis_finalized && !result.stale

  if (finalized) {
    return (
      <section className="panel next-steps next-done" aria-label="다음 단계">
        <h3 className="panel-title next-title">
          <span className="next-check" aria-hidden="true">✓</span> 회의 분석이 확정되었습니다.
        </h3>
        <p className="small next-desc">다음 작업을 선택하세요.</p>
        <div className="next-choices">
          <div className="next-choice">
            <button type="button" className="btn-primary" onClick={onNextMeeting}>다음 회의 등록</button>
            <span className="muted small">후속 회의에서 이전 Action의 이행 여부를 추적합니다.</span>
          </div>
          <div className="next-choice">
            <button type="button" className="btn-secondary" onClick={onMeetingList}>회의 목록으로</button>
            <span className="muted small">저장된 회의와 분석 결과를 확인합니다.</span>
          </div>
        </div>
        {reanalyzeError && <p className="error small" role="alert">{reanalyzeError}</p>}
        <p className="small next-desc">
          <button type="button" className="btn-link" disabled={reanalyzing} onClick={onRerun}>
            {reanalyzing ? 'AI 다시 분석 중…' : '🔄 AI 재분석'}
          </button>{' '}
          <span className="muted">결과가 마음에 들지 않으면 같은 녹취로 다시 분석합니다 (확정은 해제됩니다).</span>
        </p>
      </section>
    )
  }

  const startEdit = () => {
    setEditing(true)
    openDetails('speaker-roster')
  }

  return (
    <section className="panel next-steps" aria-label="다음 단계">
      <h3 className="panel-title next-title">다음 단계</h3>
      <p className="small next-desc">현재 회의 분석이 완료되었습니다.</p>
      {rejected > 0 && (
        <p className="small next-desc">
          Reviewer가 Action 후보 {rejected}건을 제외했습니다.
          <br />
          필요하면 검증 내용을 확인하거나 분석 결과를 수정할 수 있습니다.
        </p>
      )}
      {result.actions.length === 0 && (
        <p className="muted small next-desc">Action이 없어도 정상적인 분석 결과일 수 있으며, 그대로 확정할 수 있습니다.</p>
      )}
      {result.stale && (
        <p className="small next-stale" role="status">
          화자 정보가 변경되어 재분석 후에 확정할 수 있습니다.{' '}
          <button type="button" className="btn-link" disabled={reanalyzing} onClick={onReanalyze}>
            {reanalyzing ? '다시 분석 중…' : '변경된 화자로 다시 분석'}
          </button>
        </p>
      )}
      {editing && !result.stale && (
        <div className="next-edit" aria-label="분석 결과 수정 안내">
          <p className="small"><strong>분석 결과 수정</strong> · 원본 녹취는 바뀌지 않고 화자 정보만 수정됩니다.</p>
          <ol className="small next-edit-list">
            <li>
              화자 정보 수정 — 녹취 전문에서 ✎를 눌러 이름·부/과를 고칩니다.{' '}
              <button type="button" className="btn-link" onClick={() => openDetails('transcript-panel')}>녹취 전문 열기</button>
            </li>
            <li>
              Reviewer 제외 항목 확인 — 확인만 할 수 있으며 제외 결과를 승인(PASS)으로 바꿀 수는 없습니다.{' '}
              <button type="button" className="btn-link" onClick={() => openDetails('ai-verification')}>제외 항목 {rejected}건 보기</button>
            </li>
            <li>화자 이름이 바뀌면 재분석 안내가 나타납니다. 자동으로 다시 분석하지 않습니다.</li>
          </ol>
        </div>
      )}
      {finalizeError && <p className="error small" role="alert">{finalizeError}</p>}
      {reanalyzeError && <p className="error small" role="alert">{reanalyzeError}</p>}
      <div className="next-buttons">
        <span className="next-left">
          <button type="button" className="btn-secondary" disabled={reanalyzing} onClick={startEdit}>분석 결과 수정</button>
          <button type="button" className="btn-secondary" disabled={reanalyzing} onClick={onRerun}>
            {reanalyzing ? 'AI 다시 분석 중…' : '🔄 AI 재분석'}
          </button>
        </span>
        <button type="button" className="btn-primary" disabled={finalizing || result.stale} onClick={onFinalize}>
          {finalizing ? '확정 중…' : '회의 분석 확정'}
        </button>
      </div>
    </section>
  )
}

interface ViewProps {
  result: AnalysisResult
  onActionUpdated: (a: ActionView) => void
  onSpeakerSaved: (list: SpeakerList) => void
  onReanalyze: () => void
  onRerun: () => void
  reanalyzing: boolean
  reanalyzeError?: string
  onFinalize: () => void
  finalizing: boolean
  finalizeError?: string
  onNextMeeting: () => void
  onMeetingList: () => void
  onAdopt: (index: number, input: AdoptInput) => Promise<void>
}

export default function AnalysisView({ result, onActionUpdated, onSpeakerSaved, onReanalyze, onRerun, reanalyzing, reanalyzeError, onFinalize, finalizing, finalizeError, onNextMeeting, onMeetingList, onAdopt }: ViewProps) {
  return (
    <>
      {result.stale && <StaleBanner onReanalyze={onReanalyze} busy={reanalyzing} error={reanalyzeError} />}
      <TranscriptPanel result={result} onSaved={onSpeakerSaved} />
      {/* stale 결과는 최신 결과처럼 보이거나 조작되지 않도록 흐리게 표시하고 클릭을 막는다 (서버도 stale 승인을 거부한다). */}
      <div className={result.stale ? 'stale-result' : undefined} aria-disabled={result.stale || undefined}>
      <ResultHeader result={result} />
      <CollabPanel result={result} />
      <FollowupPanel result={result} onUpdated={onActionUpdated} />
      {result.new_actions_error && (
        <p className="error" role="alert">신규 Action 추출 중 문제가 발생했습니다. 이전 지시 이행점검 결과는 유지되었습니다.</p>
      )}
      <ActionList actions={result.actions} meetingId={result.meeting_id} rejectedCount={result.rejected.length}
        showNoActionNote={!result.new_actions_error} />
      <NeedsConfirmation items={result.needs_confirmation} />
      <div className="two-col">
        <Box cls="decision" title="✓ 결정사항" sub="회의에서 확정된 내용" items={result.decisions} mark="✓" empty="확정된 결정사항이 없습니다." />
        <Box cls="open" title="◌ 미결사항" sub="추가 논의 또는 확인이 필요한 내용" items={result.open_issues} mark="◌" empty="남은 미결사항이 없습니다." />
      </div>
      <Summary items={result.summary} />
      <AiVerification rejected={result.rejected} onAdopt={onAdopt} disabled={result.stale} />
      </div>
      {/* 다음 단계는 stale 흐림 영역 밖에 둔다: stale이어도 안내(재분석 필요)를 보여 주고 확정만 막는다. */}
      <NextSteps result={result} onFinalize={onFinalize} finalizing={finalizing} finalizeError={finalizeError}
        onReanalyze={onReanalyze} onRerun={onRerun} reanalyzing={reanalyzing} reanalyzeError={reanalyzeError}
        onNextMeeting={onNextMeeting} onMeetingList={onMeetingList} />
    </>
  )
}
