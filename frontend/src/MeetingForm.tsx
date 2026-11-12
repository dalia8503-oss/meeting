import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import {
  analyzeMeeting, createMeeting, fetchMeetings, saveSpeakerMap, transcribeMeeting,
  type ActionView, type AnalysisResult, type MeetingCreated, type SpeakerList,
} from './api'
import AnalysisView, { Progress, type Phase } from './AnalysisView'
import PreviousMeetings, { type MeetingsLoad } from './PreviousMeetings'
import SpeakerConfirm from './SpeakerConfirm'
import { toSpeakerMap, type SpeakerInputs } from './speakerInputs'

type State =
  | { s: 'idle' }
  | { s: 'uploading' }
  | {
      s: 'success'
      meeting: MeetingCreated
      preparing?: boolean // STT 준비(화자 목록 만드는 중)
      speakers?: SpeakerList // STT 준비가 끝나 화자 확인을 기다림
      analyzing?: boolean
      error?: string
      result?: AnalysisResult
    }
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
  const [prevLoad, setPrevLoad] = useState<MeetingsLoad>({ s: 'loading' })
  const [previousIds, setPreviousIds] = useState<string[]>([])
  const [speakerInputs, setSpeakerInputs] = useState<SpeakerInputs>({})
  const busy = state.s === 'uploading'

  /** 서버의 회의 목록으로 이전 회의 후보를 (재)로드한다. 현재 회의는 후보에서 제외한다. 실패해도 분석은 계속 가능하다. */
  const loadPrevious = () => {
    setPrevLoad({ s: 'loading' })
    return fetchMeetings()
      .then((list) => setPrevLoad({ s: 'ok', meetings: list }))
      .catch(() => setPrevLoad({ s: 'error' }))
  }

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
      void loadPrevious() // 서버에 저장된 이전 회의 후보 로드
    } catch (err) {
      setState({ s: 'error', message: err instanceof Error ? err.message : '회의 등록에 실패했습니다.' })
    }
  }

  const reset = () => {
    setTitle('')
    setFile(null)
    setPreviousIds([])
    setSpeakerInputs({})
    setState({ s: 'idle' })
  }

  /** 1단계: STT 준비. 원본 transcript의 speaker_N 목록을 받아 "화자 확인" 단계를 보여준다. 분석은 아직 하지 않는다. */
  const prepare = async (meeting: MeetingCreated) => {
    setState({ s: 'success', meeting, preparing: true })
    try {
      const speakers = await transcribeMeeting(meeting.meeting_id)
      // 입력창은 비워 둔다. 이름은 사용자가 직접 입력한 것만 쓴다 (서버가 이미 확인한 값이 있으면 그 값만 복원).
      setSpeakerInputs(
        Object.fromEntries(speakers.speakers.map((s) => [s.speaker, { name: s.confirmed_name ?? '', unsure: false }])),
      )
      setState({ s: 'success', meeting, speakers })
    } catch (err) {
      setState({ s: 'success', meeting, error: err instanceof Error ? err.message : '음성 변환에 실패했습니다.' })
    }
  }

  /** 2단계: 사용자가 확인한 화자(미확인 포함)를 저장한 뒤 분석한다. */
  const analyze = async (meeting: MeetingCreated, speakers: SpeakerList) => {
    setState({ s: 'success', meeting, speakers, analyzing: true })
    try {
      await saveSpeakerMap(meeting.meeting_id, toSpeakerMap(speakers, speakerInputs))
      // 이전 회의가 없으면 [] 를 보낸다 (null이 아님).
      const result = await analyzeMeeting(meeting.meeting_id, previousIds)
      setState({ s: 'success', meeting, result })
      void loadPrevious() // 방금 분석한 회의를 다음 회의의 이전 회의 후보로 반영 (reload 없음)
    } catch (err) {
      setState({ s: 'success', meeting, speakers, error: err instanceof Error ? err.message : '회의 분석에 실패했습니다.' })
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
    state.s === 'success' ? (state.result ? 'done' : state.analyzing || state.preparing ? 'analyzing' : 'uploaded') : 'idle'

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
            {!state.result && !state.speakers && (
              <button type="button" className="btn-primary" disabled={state.preparing} onClick={() => prepare(state.meeting)}>
                {state.preparing ? '음성 변환 중…' : state.error ? '다시 시도' : '회의 분석 시작'}
              </button>
            )}
            <button type="button" className="btn-link" disabled={state.analyzing || state.preparing} onClick={reset}>
              새 회의 등록
            </button>
          </div>
        </section>
        {!state.result && (
          <PreviousMeetings
            load={prevLoad.s === 'ok' ? { s: 'ok', meetings: prevLoad.meetings.filter((m) => m.meeting_id !== state.meeting.meeting_id) } : prevLoad}
            selected={previousIds}
            onChange={setPreviousIds}
            disabled={!!state.analyzing}
          />
        )}
        {!state.result && state.speakers && (
          <>
            <SpeakerConfirm list={state.speakers} inputs={speakerInputs} onChange={setSpeakerInputs} disabled={!!state.analyzing} />
            <div className="actions">
              <button
                type="button"
                className="btn-primary"
                disabled={state.analyzing}
                onClick={() => state.speakers && analyze(state.meeting, state.speakers)}
              >
                {state.error ? '다시 분석' : '분석 시작'}
              </button>
            </div>
          </>
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
