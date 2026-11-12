import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import { analyzeMeeting, createMeeting, type ActionView, type AnalysisResult, type MeetingCreated } from './api'
import AnalysisView, { Progress, type Phase } from './AnalysisView'
import PreviousMeetings from './PreviousMeetings'
import { loadKnownMeetings, rememberMeeting, type KnownMeeting } from './knownMeetings'

type State =
  | { s: 'idle' }
  | { s: 'uploading' }
  | { s: 'success'; meeting: MeetingCreated; analyzing?: boolean; error?: string; result?: AnalysisResult }
  | { s: 'error'; message: string }

const ACCEPT = '.mp3,.wav,.m4a'
const today = () => new Date().toISOString().slice(0, 10)

export default function MeetingForm() {
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(today())
  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [state, setState] = useState<State>({ s: 'idle' })
  const input = useRef<HTMLInputElement>(null)
  const [known, setKnown] = useState<KnownMeeting[]>(loadKnownMeetings)
  const [previousIds, setPreviousIds] = useState<string[]>([])
  const busy = state.s === 'uploading'

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const f = e.dataTransfer.files?.[0]
    if (f) setFile(f)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return
    // UX 보조 검증. 실제 검증은 Backend가 다시 한다.
    if (!title.trim()) return setState({ s: 'error', message: '회의명을 입력해 주세요.' })
    if (!date) return setState({ s: 'error', message: '회의일을 입력해 주세요.' })
    if (!file) return setState({ s: 'error', message: '회의 녹음 파일을 선택해 주세요.' })
    setState({ s: 'uploading' })
    try {
      const meeting = await createMeeting({ title: title.trim(), meetingDate: date, file })
      setState({ s: 'success', meeting })
    } catch (err) {
      setState({ s: 'error', message: err instanceof Error ? err.message : '회의 등록에 실패했습니다.' })
    }
  }

  const reset = () => {
    setTitle('')
    setFile(null)
    setPreviousIds([])
    setState({ s: 'idle' })
  }

  const analyze = async (meeting: MeetingCreated) => {
    setState({ s: 'success', meeting, analyzing: true })
    try {
      // 이전 회의가 없으면 [] 를 보낸다 (null이 아님).
      const result = await analyzeMeeting(meeting.meeting_id, previousIds)
      setKnown(rememberMeeting({ meeting_id: meeting.meeting_id, title: meeting.title, meeting_date: meeting.meeting_date }))
      setState({ s: 'success', meeting, result })
    } catch (err) {
      setState({ s: 'success', meeting, error: err instanceof Error ? err.message : '회의 분석에 실패했습니다.' })
    }
  }

  /** 사람 판단 API가 돌려준 최신 Action으로 화면 state를 교체한다 (reload 없음). */
  const replaceAction = (updated: ActionView) =>
    setState((cur) => {
      if (cur.s !== 'success' || !cur.result) return cur
      const swap = (list: ActionView[]) => list.map((a) => (a.action_id === updated.action_id ? updated : a))
      return { ...cur, result: { ...cur.result, actions: swap(cur.result.actions), new_actions: swap(cur.result.new_actions) } }
    })

  const phase: Phase =
    state.s === 'success' ? (state.result ? 'done' : state.analyzing ? 'analyzing' : 'uploaded') : 'idle'

  if (state.s === 'success') {
    return (
      <>
        <section className="panel meeting-head">
          <div className="mh-info">
            <h2 className="mh-title">{state.meeting.title}</h2>
            <span className="muted small">회의일 {state.meeting.meeting_date}</span>
            <span className="muted small">🎙 {state.meeting.audio_filename}</span>
          </div>
          <div className="mh-actions">
            {!state.result && (
              <button type="button" className="btn-primary" disabled={state.analyzing} onClick={() => analyze(state.meeting)}>
                {state.error ? '다시 분석' : '회의 분석 시작'}
              </button>
            )}
            <button type="button" className="btn-link" disabled={state.analyzing} onClick={reset}>
              새 회의 등록
            </button>
          </div>
        </section>
        {!state.result && (
          <PreviousMeetings
            meetings={known.filter((k) => k.meeting_id !== state.meeting.meeting_id)}
            selected={previousIds}
            onChange={setPreviousIds}
            disabled={!!state.analyzing}
          />
        )}
        {state.error && <p className="error" role="alert">{state.error}</p>}
        <Progress phase={phase} />
        {state.result && <AnalysisView result={state.result} onActionUpdated={replaceAction} />}
      </>
    )
  }

  return (
    <>
    <form className="panel meeting-form" onSubmit={submit} noValidate>
      <h2 className="panel-title">회의 등록</h2>
      <div className="form-row">
        <label>
          회의명
          <input value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} />
        </label>
        <label>
          회의일
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} disabled={busy} />
        </label>
      </div>
      <div
        className={`drop${drag ? ' drop-over' : ''}`}
        onClick={() => !busy && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
      >
        <strong>🎙 회의 녹음 파일</strong>
        <span className="muted small">MP3 / WAV / M4A</span>
        <span>{file ? file.name : '파일을 선택하거나 여기에 놓으세요'}</span>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </div>
      {busy && <p className="muted">회의 파일을 업로드하고 있습니다...</p>}
      {state.s === 'error' && <p className="error" role="alert">{state.message}</p>}
      <div className="actions">
        <button type="submit" className="btn-primary" disabled={busy}>
          회의 등록
        </button>
      </div>
    </form>
    <Progress phase={phase} />
    </>
  )
}
