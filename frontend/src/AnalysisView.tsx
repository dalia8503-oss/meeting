import type { ActionView, AnalysisResult, SummaryItem } from './api'

const STEPS = ['음성 업로드', 'STT 변환', '회의 분석', 'Reviewer 검증', 'Action 생성']

/** 서버가 세부 진행률을 주지 않으므로 퍼센트 없이 "진행 중"만 보여준다. */
export function AnalysisProgress({ done }: { done: boolean }) {
  return (
    <>
      <ol className="steps" aria-label="분석 단계">
        {STEPS.map((s, i) => (
          <li key={s} className={done || i === 0 ? 'step-done' : 'step-wait'}>
            {done || i === 0 ? '✓' : '…'} {s}
          </li>
        ))}
      </ol>
      {!done && <p className="muted">회의를 분석하고 있습니다. 잠시만 기다려 주세요...</p>}
    </>
  )
}

function Section({ title, items }: { title: string; items: SummaryItem[] }) {
  if (items.length === 0) return null
  return (
    <section>
      <h3>{title}</h3>
      <ul>
        {items.map((it, i) => (
          <li key={i}>
            {it.text} <span className="muted tiny">[{it.evidence_timestamps.join(', ')}]</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ActionCard({ a }: { a: ActionView }) {
  return (
    <li className="action">
      <div>
        <strong>{a.action_id}</strong> <span className="badge">{a.status}</span>
        {a.flags.map((f) => (
          <span key={f} className="badge badge-error">{f}</span>
        ))}
      </div>
      <div>{a.task}</div>
      <div className="muted small">
        담당 {a.assignee || '미지정'} · 기한 {a.due_date ?? '미지정'}
        {a.due_date_normalized ? ` (${a.due_date_normalized})` : ''}
      </div>
      <blockquote className="muted small">
        [{a.evidence.timestamp}] {a.evidence.speaker ? `${a.evidence.speaker}: ` : ''}
        {a.evidence.text}
      </blockquote>
    </li>
  )
}

export default function AnalysisView({ result }: { result: AnalysisResult }) {
  return (
    <div>
      <AnalysisProgress done />
      <Section title="회의 요약" items={result.summary} />
      <Section title="결정사항" items={result.decisions} />
      <Section title="미결사항" items={result.open_issues} />
      <Section title="확인 필요" items={result.needs_confirmation} />
      <section>
        <h3>Action List ({result.actions.length})</h3>
        {result.actions.length === 0 ? (
          <p className="muted">이 회의에서 생성된 Action이 없습니다.</p>
        ) : (
          <ul className="action-list">
            {result.actions.map((a) => (
              <ActionCard key={a.action_id} a={a} />
            ))}
          </ul>
        )}
        {result.rejected.length > 0 && (
          <p className="muted small">Reviewer 미통과로 제외된 후보 {result.rejected.length}건 (Action으로 생성되지 않음)</p>
        )}
      </section>
    </div>
  )
}
