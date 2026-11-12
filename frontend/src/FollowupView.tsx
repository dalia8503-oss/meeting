import { useState } from 'react'
import {
  reviewAction,
  withdrawCompletion,
  type ActionView,
  type AgentRun,
  type AnalysisResult,
  type Evidence,
  type FollowupResult,
  type ReviewChoice,
} from './api'
import { StatusBadge } from './StatusBadge'

/** "2026-11-12T14:32:10+09:00" → "2026-11-12 14:32". 시각 변환 없이 API 값의 앞부분만 쓴다. */
function formatApprovedAt(v: string | undefined): string {
  return v ? v.replace('T', ' ').slice(0, 16) : ''
}

export function ApprovalInfo({ a }: { a: ActionView }) {
  const h = a.approved_by_human
  if (a.status !== '완료' || !h?.approver) return null
  return (
    <div className="approval small muted">
      ✓ {h.approver} 승인{h.approved_at ? <> · {formatApprovedAt(h.approved_at)}</> : null}
    </div>
  )
}

// ------------------------------------------------------------------ AI 협업 분석

const AGENTS: Record<string, { icon: string; name: string; role: string }> = {
  'meeting-analyst': { icon: '🤖', name: 'Meeting Analyst', role: '신규 지시 분석' },
  'followup-agent': { icon: '🔎', name: 'Follow-up Agent', role: '이전 지시 이행점검' },
}

function seconds(a: AgentRun) {
  return `${Math.max(0, a.finished_at - a.started_at).toFixed(1)}초`
}

function Reviewer({ counts }: { counts: Record<string, number> }) {
  const pass = counts.PASS ?? 0
  const reject = counts.REJECT ?? 0
  const checked = pass + reject > 0
  return (
    <li className="agent">
      <div className="agent-name">🛡 Reviewer</div>
      <div className="muted small">독립 검증</div>
      <div className="agent-state">
        {checked ? (
          <>
            검증 완료 <span className="muted small">PASS {pass} · REJECT {reject}</span>
          </>
        ) : (
          <span className="muted">검증 대상 없음</span>
        )}
      </div>
    </li>
  )
}

export function CollabPanel({ result }: { result: AnalysisResult }) {
  const ex = result.execution
  if (!ex) {
    return (
      <section className="panel collab" aria-label="AI 협업 분석">
        <h3 className="panel-title">AI 협업 분석</h3>
        <p className="muted small">AI Agent 분석 완료</p>
      </section>
    )
  }
  const parallel = ex.mode === 'parallel'
  const span = Math.max(...ex.agents.map((a) => a.finished_at), 0.001)
  let sub = 'AI Agent 분석 완료'
  if (parallel) sub = ex.overlapped ? `${ex.agents.length}개 분석 Agent 병렬 실행` : `${ex.agents.length}개 분석 Agent 실행`
  return (
    <section className="panel collab" aria-label="AI 협업 분석">
      <h3 className="panel-title">
        AI 협업 분석 <span className="count-sm">{sub}</span>
      </h3>
      <ul className="agents">
        {ex.agents.map((a) => {
          const meta = AGENTS[a.agent] ?? { icon: '🤖', name: a.agent, role: '' }
          return (
            <li key={a.agent} className="agent">
              <div className="agent-name">
                {meta.icon} {meta.name}
              </div>
              {meta.role && <div className="muted small">{meta.role}</div>}
              <div className="agent-state">
                {a.status === 'ok' ? '완료' : <span className="agent-err">오류</span>}{' '}
                <span className="muted small">{seconds(a)}</span>
              </div>
              {parallel && (
                <div className="bar-track" aria-hidden="true">
                  <div
                    className="bar"
                    style={{
                      left: `${(a.started_at / span) * 100}%`,
                      width: `${Math.max(4, ((a.finished_at - a.started_at) / span) * 100)}%`,
                    }}
                  />
                </div>
              )}
            </li>
          )
        })}
        <Reviewer counts={ex.reviewer} />
      </ul>
    </section>
  )
}

// ------------------------------------------------------------------ 이전 지시 이행점검

function EvidenceQuote({ e }: { e: Evidence }) {
  return (
    <div className="evidence">
      <div className="ev-meta">
        🎙 {e.timestamp}
        {e.speaker && (
          <span className="ev-speaker">
            {e.speaker}
            {e.speaker_name ? ` · ${e.speaker_name}` : ''}
          </span>
        )}
      </div>
      <blockquote>“{e.text}”</blockquote>
    </div>
  )
}

const OUTCOME_NOTE: Record<string, string> = {
  rejected: 'Reviewer가 근거를 인정하지 않아 상태를 바꾸지 않았습니다.',
  skipped: '이번 회의에서는 점검하지 않았습니다.',
}

interface CardProps {
  r: FollowupResult
  action: ActionView | undefined
  meetingId: string
  actor: string
  onUpdated: (a: ActionView) => void
  busy: string | null
  setBusy: (id: string | null) => void
}

function FollowupCard({ r, action, meetingId, actor, onUpdated, busy, setBusy }: CardProps) {
  const [error, setError] = useState('')
  const current = action?.status
  const applied = r.outcome === 'applied' || r.outcome === 'reassessed'
  const rep = r.evidence[0] // 대표 근거: 이행점검이 제시한 첫 근거. 임의 생성 없음.
  const isCandidate = current === '완료 후보'
  const working = busy === r.action_id

  const run = async (fn: () => Promise<ActionView>) => {
    setError('')
    setBusy(r.action_id)
    try {
      onUpdated(await fn())
    } catch (err) {
      setError(err instanceof Error ? err.message : '처리에 실패했습니다.')
    } finally {
      setBusy(null)
    }
  }

  const needActor = () => {
    if (actor.trim()) return false
    setError('승인자 이름을 입력해 주세요.')
    return true
  }

  const choose = (choice: ReviewChoice) => {
    if (needActor()) return
    const name = actor.trim()
    const body =
      choice === '완료' && rep
        ? { choice, actor_name: name, meeting_id: meetingId, timestamp: rep.timestamp }
        : { choice, actor_name: name }
    void run(() => reviewAction(r.action_id, body))
  }

  const withdraw = () => {
    if (needActor()) return
    if (!window.confirm(`${r.action_id}의 완료를 철회할까요? 상태가 '진행중'으로 돌아갑니다.`)) return
    void run(() => withdrawCompletion(r.action_id, actor.trim()))
  }

  return (
    <li className="fu-card">
      <div className="fu-head">
        <strong className="fu-id">{r.action_id}</strong>
        <span className="fu-assignee">{action?.assignee || '담당자 미지정'}</span>
        <span className="fu-task">{action?.task ?? ''}</span>
      </div>

      <div className="fu-flow">
        <div className="fu-step">
          <span className="muted tiny">기존 상태</span>
          {r.from_status ? <StatusBadge status={r.from_status} /> : <span className="muted">-</span>}
        </div>
        <span className="fu-arrow" aria-hidden="true">→</span>
        <div className="fu-step">
          <span className="muted tiny">현재 판정</span>
          {applied && r.to_status ? <StatusBadge status={r.to_status} /> : <span className="muted small">변경 없음</span>}
        </div>
        {current && current !== r.to_status && (
          <>
            <span className="fu-arrow" aria-hidden="true">→</span>
            <div className="fu-step">
              <span className="muted tiny">사람 판단 후</span>
              <StatusBadge status={current} />
            </div>
          </>
        )}
      </div>
      {!applied && OUTCOME_NOTE[r.outcome] && <p className="muted small fu-note">{OUTCOME_NOTE[r.outcome]}</p>}

      {r.evidence.length > 0 && (
        <div className="fu-evidence">
          <div className="muted tiny">근거</div>
          {r.evidence.map((e, i) => (
            <EvidenceQuote key={i} e={e} />
          ))}
        </div>
      )}

      <div className="fu-reviewer muted small">
        Reviewer{' '}
        {r.reviewer_verdict ? (
          <span className={`verdict verdict-${r.reviewer_verdict === 'PASS' ? 'pass' : 'reject'}`}>{r.reviewer_verdict}</span>
        ) : (
          <span>-</span>
        )}
      </div>

      {isCandidate && (
        <div className="review-box" aria-label="사람 최종 판단">
          <div className="review-title">사람 최종 판단</div>
          <div className="review-btns">
            <button type="button" className="btn-done" disabled={working || !rep} onClick={() => choose('완료')}>
              완료
            </button>
            <button type="button" className="btn-sub" disabled={working} onClick={() => choose('계속 진행')}>
              계속 진행
            </button>
            <button type="button" className="btn-sub" disabled={working} onClick={() => choose('확인 필요')}>
              확인 필요
            </button>
          </div>
          {!rep && <p className="muted small">승인 근거(이행점검 근거)가 없어 완료할 수 없습니다.</p>}
        </div>
      )}

      {action && <ApprovalInfo a={action} />}
      {current === '완료' && (
        <button type="button" className="btn-link btn-withdraw" disabled={working} onClick={withdraw}>
          완료 철회
        </button>
      )}
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
    </li>
  )
}

export function FollowupPanel({ result, onUpdated }: { result: AnalysisResult; onUpdated: (a: ActionView) => void }) {
  const [actor, setActor] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const items = result.followup_results
  if (items.length === 0 && result.previous_meeting_ids.length === 0) return null
  const needsHuman = result.actions.some((a) => a.status === '완료 후보' || a.status === '완료')
  return (
    <section className="panel followup-panel" aria-label="이전 지시 이행점검">
      <div className="panel-head">
        <h3 className="panel-title">이전 지시 이행점검</h3>
        <span className="count-sm">
          이전 회의 {result.previous_meeting_ids.length}건 · 점검 {items.length}건
        </span>
      </div>
      {result.followup_error && (
        <p className="error small" role="alert">
          이행점검 중 문제가 발생했습니다. 신규 Action 분석 결과는 유지되었습니다.
        </p>
      )}
      {needsHuman && (
        <label className="actor">
          승인자 이름
          <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="예: 홍길동" maxLength={40} />
        </label>
      )}
      {items.length === 0 ? (
        <p className="muted small">이행점검 대상 Action이 없습니다.</p>
      ) : (
        <ul className="fu-list">
          {items.map((r) => (
            <FollowupCard
              key={r.action_id}
              r={r}
              action={result.actions.find((a) => a.action_id === r.action_id)}
              meetingId={result.meeting_id}
              actor={actor}
              onUpdated={onUpdated}
              busy={busy}
              setBusy={setBusy}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
