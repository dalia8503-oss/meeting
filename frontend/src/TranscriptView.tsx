import { useState } from 'react'
import { saveSpeakerMap, type AnalysisResult, type SpeakerList, type SpeakerMapValue, type TranscriptLine } from './api'

/** 분석 후 transcript에서 화자 이름을 고친다. 원본 transcript text·speaker 라벨은 바뀌지 않고 회의별 speaker_map만 바뀐다. */
export function TranscriptPanel({ result, onSaved }: { result: AnalysisResult; onSaved: (list: SpeakerList) => void }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dept, setDept] = useState('')
  const [sect, setSect] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // 이름 수정은 STT 원본 라벨(speaker_N)에만 가능하다.
  const editable = (label: string) => /^speaker_\d+$/.test(label)
  const profiles = new Map<string, TranscriptLine>()
  for (const t of result.transcript) if (!profiles.has(t.speaker)) profiles.set(t.speaker, t)

  const open = (label: string) => {
    const p = profiles.get(label)
    setEditing(label)
    setDraft(p?.speaker_name ?? '')
    setDept(p?.speaker_department ?? '')
    setSect(p?.speaker_section ?? '')
    setError('')
    setNotice('')
  }

  const save = async (label: string, name: string | null) => {
    setBusy(true)
    setError('')
    try {
      // 다른 화자의 확인된 이름·부/과는 그대로 다시 보낸다 (전체 교체 API).
      const map: Record<string, SpeakerMapValue> = {}
      for (const [l, p] of profiles) {
        if (editable(l)) {
          map[l] = p.speaker_name
            ? { name: p.speaker_name, department: p.speaker_department ?? null, section: p.speaker_section ?? null }
            : null
        }
      }
      map[label] = name && name.trim()
        ? { name: name.trim(), department: dept.trim() || null, section: sect.trim() || null }
        : null
      const saved = await saveSpeakerMap(result.meeting_id, map)
      onSaved(saved)
      setEditing(null)
      setNotice(saved.stale
        ? '이름이 바뀌어 재분석이 필요합니다. 위의 "변경된 화자로 다시 분석"을 눌러 주세요.'
        : '저장했습니다. 부/과 정보는 표시만 갱신되며 재분석은 필요하지 않습니다.')
    } catch (err) {
      setError(err instanceof Error ? err.message : '화자 수정에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  const roster = [...profiles.values()].filter((p) => editable(p.speaker))
  const form = (label: string) => (
    <div className="speaker-edit">
      <label>
        부
        <input value={dept} placeholder="예: 1부" disabled={busy} onChange={(e) => setDept(e.target.value)} />
      </label>
      <label>
        과
        <input value={sect} placeholder="예: 3과" disabled={busy} onChange={(e) => setSect(e.target.value)} />
      </label>
      <label>
        이름/직급
        <input value={draft} placeholder="이름 또는 역할" disabled={busy} onChange={(e) => setDraft(e.target.value)} />
      </label>
      <button type="button" className="btn-secondary" disabled={busy || !draft.trim()} onClick={() => save(label, draft)}>
        저장
      </button>
      <button type="button" className="btn-link" disabled={busy} onClick={() => save(label, null)}>
        미확인으로
      </button>
      <button type="button" className="btn-link" disabled={busy} onClick={() => setEditing(null)}>
        취소
      </button>
    </div>
  )

  return (
    <>
    <section id="speaker-roster" className="panel roster-panel" aria-label="화자 정보">
      <h3 className="panel-title">👥 화자 정보</h3>
      <p className="muted small">
        ✎로 부/과/이름을 수정합니다. 원본 녹취와 speaker_id는 바뀌지 않습니다. 이름이 바뀌면 재분석이 필요하고, 부/과만 바뀌면 표시만 갱신됩니다.
      </p>
      {error && <p className="error small" role="alert">{error}</p>}
      {notice && <p className="small" role="status">{notice}</p>}
      <ul className="roster-list">
        {roster.map((p) => (
          <li key={p.speaker} className="roster-item">
            <span className="muted small">{p.speaker}</span>{' '}
            <strong>{p.speaker_display ?? p.speaker_name ?? '미확인'}</strong>
            <button type="button" className="btn-link" aria-label={`${p.speaker} 화자 수정`} onClick={() => open(p.speaker)}>
              ✎
            </button>
            {editing === p.speaker && form(p.speaker)}
          </li>
        ))}
      </ul>
    </section>
    <details id="transcript-panel" className="panel transcript-panel">
      <summary>
        녹취 전문 <span className="muted small">· 발화 옆 ✎로도 화자 정보를 수정할 수 있습니다</span>
      </summary>
      <ul className="transcript-list">
        {result.transcript.map((t, i) => (
          <li key={`${t.timestamp}-${i}`} className="transcript-line">
            <span className="muted small">{t.timestamp}</span>{' '}
            <strong>{t.speaker_display ?? t.speaker_name ?? t.speaker}</strong>
            {t.speaker_name && <span className="muted tiny"> ({t.speaker})</span>}
            {editable(t.speaker) && (
              <button type="button" className="btn-link" aria-label={`${t.speaker} 화자 수정`}
                onClick={() => { open(t.speaker); document.getElementById('speaker-roster')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>
                ✎
              </button>
            )}
            <div className="transcript-text">“{t.text}”</div>
          </li>
        ))}
      </ul>
    </details>
    </>
  )
}

/** 분석 이후 화자가 바뀌었을 때의 안내. 자동 재분석은 하지 않으며 사용자가 버튼을 눌러야 한다. */
export function StaleBanner({ onReanalyze, busy, error }: { onReanalyze: () => void; busy: boolean; error?: string }) {
  return (
    <section className="panel stale-banner" role="alert" aria-label="재분석 필요">
      <strong>⚠ 화자 정보가 변경되었습니다.</strong>
      <p className="small">
        담당자 및 Action 판단에 영향을 줄 수 있어 재분석이 필요합니다. 아래 결과는 이전 화자 정보 기준이므로 최신 결과로 사용하지 마세요.
      </p>
      {error && <p className="error small">{error}</p>}
      <button type="button" className="btn-primary" disabled={busy} onClick={onReanalyze}>
        {busy ? '다시 분석 중…' : '변경된 화자로 다시 분석'}
      </button>
    </section>
  )
}
