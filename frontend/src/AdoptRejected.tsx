import { useState } from 'react'
import type { RejectedItem } from './api'

export interface AdoptInput {
  actorName: string
  assignees: string[]
  assigner: string
}

const LABEL = /^(speaker_\d+|확인 필요)?$/

/**
 * Reviewer가 제외한 후보 목록. 사람이 근거를 확인하고 명시적으로 "Action으로 채택"할 수 있다.
 * Reviewer 판정(REJECT)은 바뀌지 않는다. 채택된 Action은 서버가 'Reviewer미통과' 표시·채택자 기록과 함께 '확인 필요' 상태로 만든다.
 */
export function RejectedList({ rejected, onAdopt, disabled }: {
  rejected: RejectedItem[]
  onAdopt: (index: number, input: AdoptInput) => Promise<void>
  disabled: boolean
}) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const [actor, setActor] = useState('')
  const [assignees, setAssignees] = useState('')
  const [assigner, setAssigner] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const open = (r: RejectedItem) => {
    setOpenIndex(r.index)
    setAssignees(LABEL.test(r.assignee) ? '' : r.assignee) // 화자 라벨·미확인은 비워 둔다 (사람이 직접 지정)
    setAssigner(LABEL.test(r.assigner) ? '' : r.assigner)
    setError('')
  }

  const submit = async (r: RejectedItem) => {
    setBusy(true)
    setError('')
    try {
      await onAdopt(r.index, {
        actorName: actor.trim(),
        assignees: assignees.split(/[,，]/).map((x) => x.trim()).filter(Boolean),
        assigner: assigner.trim(),
      })
      setOpenIndex(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action으로 채택하지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ul className="rejected-list">
      {rejected.map((r) => {
        const adopted = r.adopted_action_ids.length > 0
        return (
          <li key={r.index} className="rejected-item">
            <div>
              {r.task} <span className="muted tiny">[{r.timestamp}] {r.assigner ? `${r.assigner} → ` : ''}{r.assignee}</span>
            </div>
            {r.reasons.length > 0 && <div className="muted tiny">제외 사유: {r.reasons.join(' / ')}</div>}
            {adopted ? (
              <div className="small rejected-adopted">
                ✓ Action으로 채택됨 ({r.adopted_action_ids.join(', ')}){r.adopted_by ? ` · 채택자 ${r.adopted_by}` : ''}
              </div>
            ) : openIndex === r.index ? (
              <div className="adopt-form">
                <p className="muted small">
                  근거 발언: “{r.evidence}”<br />
                  Reviewer 판정은 바뀌지 않습니다. 채택한 Action은 <strong>확인 필요</strong> 상태와 Reviewer미통과 표시로 등록되고, 완료는 이후 사람이 승인해야 합니다.
                </p>
                <label>
                  채택자 이름 (필수)
                  <input value={actor} disabled={busy} placeholder="예: 김부장" onChange={(e) => setActor(e.target.value)} />
                </label>
                <label>
                  담당자 (여러 명이면 쉼표로 구분)
                  <input value={assignees} disabled={busy} placeholder="녹취에 나온 이름" onChange={(e) => setAssignees(e.target.value)} />
                </label>
                <label>
                  지시자 (선택)
                  <input value={assigner} disabled={busy} placeholder="비워 두면 후보 값 유지" onChange={(e) => setAssigner(e.target.value)} />
                </label>
                {error && <p className="error small" role="alert">{error}</p>}
                <div className="adopt-buttons">
                  <button type="button" className="btn-link" disabled={busy} onClick={() => setOpenIndex(null)}>취소</button>
                  <button type="button" className="btn-primary" disabled={busy || !actor.trim()} onClick={() => submit(r)}>
                    {busy ? '채택 중…' : 'Action으로 채택'}
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" className="btn-secondary adopt-open" disabled={disabled} onClick={() => open(r)}>
                Action으로 채택
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}
