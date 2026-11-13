import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import {
  adoptRejected, analyzeMeeting, createMeeting, fetchMeetings, finalizeAnalysis, fetchResult, reanalyzeMeeting, saveSpeakerMap, suggestSpeakers, transcribeMeeting,
  type ActionView, type AnalysisResult, type MeetingCreated, type SpeakerList,
} from './api'
import BriefingCard from './BriefingCard'
import AnalysisView, { Progress, type Phase } from './AnalysisView'
import PreviousMeetings, { type MeetingsLoad } from './PreviousMeetings'
import SpeakerConfirm from './SpeakerConfirm'
import { initialInputs, pendingSpeakers, toSpeakerMap, type SpeakerInputs } from './speakerInputs'

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
  const [suggesting, setSuggesting] = useState(false)
  const [suggestError, setSuggestError] = useState('')
  const [reanalyzing, setReanalyzing] = useState(false)
  const [reanalyzeError, setReanalyzeError] = useState('')
  const [finalizing, setFinalizing] = useState(false)
  const [finalizeError, setFinalizeError] = useState('')
  // 확정 후 등록 화면으로 돌아갈 때 방금 확정한 회의를 강조만 한다 (자동 선택하지 않는다).
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const pendingScroll = useRef<'form' | 'list' | null>(null)
  const busy = state.s === 'uploading'

  /** 서버의 회의 목록으로 이전 회의 후보를 (재)로드한다. 현재 회의는 후보에서 제외한다. 실패해도 분석은 계속 가능하다. */
  const loadPrevious = () => {
    setPrevLoad({ s: 'loading' })
    return fetchMeetings()
      .then((list) => setPrevLoad({ s: 'ok', meetings: list }))
      .catch(() => setPrevLoad({ s: 'error' }))
  }

  // 등록 전 화면에서도 지난 회의 목록을 바로 볼 수 있어야 한다.
  useEffect(() => {
    let alive = true // 초기 state가 이미 loading이므로 setState는 응답 시점에만 호출한다.
    fetchMeetings()
      .then((list) => alive && setPrevLoad({ s: 'ok', meetings: list }))
      .catch(() => alive && setPrevLoad({ s: 'error' }))
    return () => {
      alive = false
    }
  }, [])

  /** 삭제 성공 후: 선택에서 제거하고(브리핑 카드도 key/조건으로 함께 사라진다) 목록을 다시 불러온다. */
  const onDeleted = (id: string) => {
    setPreviousIds((ids) => ids.filter((x) => x !== id))
    // 연결 회의와 함께 지워진 회의가 지금 화면에 열려 있으면 빈 등록 화면으로 돌아간다.
    setState((cur) => (cur.s === 'success' && cur.meeting.meeting_id === id ? { s: 'idle' } : cur))
    void loadPrevious()
  }

  // 등록 화면이 그려진 뒤에 스크롤한다.
  useEffect(() => {
    if (state.s !== 'idle' || !pendingScroll.current) return
    const target = pendingScroll.current
    pendingScroll.current = null
    if (target === 'form') {
      const f = document.getElementById('meeting-form')
      f?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      f?.querySelector('input')?.focus({ preventScroll: true })
    } else {
      document.getElementById('prev-meetings')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [state.s])

  /** 사람이 분석 결과를 확정한다. 서버가 저장에 성공한 뒤에만 확정 UI로 바뀐다. */
  const finalize = async (meeting: MeetingCreated) => {
    setFinalizing(true)
    setFinalizeError('')
    try {
      const result = await finalizeAnalysis(meeting.meeting_id)
      setState((cur) => (cur.s === 'success' && cur.meeting.meeting_id === meeting.meeting_id ? { ...cur, result } : cur))
      void loadPrevious()
    } catch (err) {
      setFinalizeError(err instanceof Error ? err.message : '회의 분석을 확정하지 못했습니다.')
    } finally {
      setFinalizing(false)
    }
  }

  /** 확정 후 이동: 기존 등록 화면(회의 등록 + 지난 회의 선택)을 그대로 쓴다. */
  const leaveTo = (meeting: MeetingCreated, target: 'form' | 'list') => {
    pendingScroll.current = target
    reset()
    setHighlightId(meeting.meeting_id)
    setFinalizeError('')
    void loadPrevious()
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
      setSpeakerInputs(initialInputs(speakers))
      setState({ s: 'success', meeting, speakers })
      void suggest(meeting, speakers) // AI 1차 추천 (실패해도 직접 입력으로 계속할 수 있다)
    } catch (err) {
      setState({ s: 'success', meeting, error: err instanceof Error ? err.message : '음성 변환에 실패했습니다.' })
    }
  }

  /** Speaker Identification Agent 1차 추천. 추천은 입력창에 미리 채워질 뿐 사람이 확인하기 전에는 확정되지 않는다. */
  const suggest = async (meeting: MeetingCreated, speakers: SpeakerList) => {
    setSuggesting(true)
    setSuggestError('')
    try {
      const { suggestions } = await suggestSpeakers(meeting.meeting_id)
      const byId = new Map(suggestions.map((x) => [x.speaker_id, x]))
      const merged: SpeakerList = { ...speakers, speakers: speakers.speakers.map((s) => ({ ...s, suggestion: byId.get(s.speaker) ?? null })) }
      setSpeakerInputs(initialInputs(merged))
      setState((cur) => (cur.s === 'success' && cur.speakers ? { ...cur, speakers: merged } : cur))
    } catch (err) {
      setSuggestError(err instanceof Error ? err.message : '화자 추천에 실패했습니다. 직접 입력해 주세요.')
    } finally {
      setSuggesting(false)
    }
  }

  /** 2단계: 사용자가 확인한 화자(미확인 포함)를 저장한 뒤 분석한다. */
  const analyze = async (meeting: MeetingCreated, speakers: SpeakerList) => {
    setState({ s: 'success', meeting, speakers, analyzing: true })
    setHighlightId(null)
    setFinalizeError('')
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

  /** 분석 후 화자 수정이 저장되었다: transcript 표시 이름과 stale 여부만 갱신한다 (분석 결과 자체는 그대로 둔다). */
  const onSpeakerSaved = (list: SpeakerList) => {
    setState((cur) => {
      if (cur.s !== 'success' || !cur.result) return cur
      const prof = new Map(list.speakers.map((s) => [s.speaker, s.confirmed_profile ?? null]))
      const transcript = cur.result.transcript.map((t) => {
        const p = prof.get(t.speaker)
        return { ...t, speaker_name: p?.name ?? null, speaker_display: p?.display_name ?? null,
          speaker_department: p?.department ?? null, speaker_section: p?.section ?? null }
      })
      // 화자가 바뀌면 서버가 확정을 해제한다. 화면도 즉시 확정 UI를 내린다 (재분석 후 다시 확정).
      return { ...cur, result: { ...cur.result, transcript, stale: !!list.stale,
        ...(list.stale ? { analysis_finalized: false, analysis_finalized_at: null } : {}) } }
    })
    // Action 담당자·근거 등 나머지 표시명은 서버가 계산한다. 저장된 결과를 다시 읽어 갱신한다 (분석을 다시 실행하지 않는다).
    fetchResult(list.meeting_id)
      .then((fresh) => setState((cur) => (cur.s === 'success' && cur.result ? { ...cur, result: fresh } : cur)))
      .catch(() => undefined)
  }

  /** 사람이 Reviewer 제외 후보를 Action으로 채택한다. 서버가 저장에 성공한 뒤 갱신된 결과로 화면을 바꾼다 (실패는 호출한 폼이 표시). */
  const adopt = async (meeting: MeetingCreated, index: number, input: { actorName: string; assignees: string[]; assigner: string }) => {
    const result = await adoptRejected(meeting.meeting_id, index, input)
    setState((cur) => (cur.s === 'success' && cur.meeting.meeting_id === meeting.meeting_id ? { ...cur, result } : cur))
    void loadPrevious()
  }

  /** 사용자가 명시적으로 누른 재분석. 자동 재분석은 하지 않는다. */
  const reanalyze = async (meeting: MeetingCreated, force = false) => {
    setReanalyzing(true)
    setReanalyzeError('')
    try {
      const result = await reanalyzeMeeting(meeting.meeting_id, force)
      setState({ s: 'success', meeting, result })
      setFinalizeError('')
      void loadPrevious()
    } catch (err) {
      setReanalyzeError(err instanceof Error ? err.message : '재분석에 실패했습니다.')
    } finally {
      setReanalyzing(false)
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
            onDeleted={onDeleted}
            disabled={!!state.analyzing || !!state.preparing}
          />
        )}
        {/* 브리핑은 사용자가 고른 이전 회의 1건 + 이 회의의 meeting_date 기준. key가 바뀌면(회의/날짜 변경) 결과가 초기화된다. */}
        {!state.result && previousIds.length === 1 && (
          <BriefingCard
            key={`${previousIds[0]}|${state.meeting.meeting_date}`}
            previousMeetingId={previousIds[0]}
            currentDate={state.meeting.meeting_date}
          />
        )}
        {!state.result && previousIds.length > 1 && (
          <p className="muted small">지난 회의 브리핑은 이전 회의를 하나만 선택했을 때 들을 수 있습니다.</p>
        )}
        {!state.result && state.speakers && (
          <>
            <SpeakerConfirm
              list={state.speakers}
              inputs={speakerInputs}
              onChange={setSpeakerInputs}
              disabled={!!state.analyzing}
              suggesting={suggesting}
              suggestError={suggestError}
              onSuggest={() => state.speakers && suggest(state.meeting, state.speakers)}
            />
            <div className="actions">
              <button
                type="button"
                className="btn-primary"
                disabled={state.analyzing || suggesting || pendingSpeakers(state.speakers, speakerInputs).length > 0}
                onClick={() => state.speakers && analyze(state.meeting, state.speakers)}
              >
                {state.error ? '화자 확인 후 다시 분석' : '화자 확인 후 분석 시작'}
              </button>
              {pendingSpeakers(state.speakers, speakerInputs).length > 0 && (
                <span className="muted small">AI 추천을 확인하거나 수정해 주세요 (자동 확정되지 않습니다).</span>
              )}
            </div>
          </>
        )}
        {state.error && <p className="error" role="alert">{state.error}</p>}
        <Progress phase={phase} />
        {state.result && (
          <AnalysisView
            result={state.result}
            onActionUpdated={replaceAction}
            onSpeakerSaved={onSpeakerSaved}
            onReanalyze={() => reanalyze(state.meeting)}
            onRerun={() => {
              // AI 재분석은 현재 결과(요약·Action 등)를 새 결과로 바꾸므로 사용자가 확인한 경우에만 실행한다.
              if (window.confirm('같은 녹취와 화자 확인으로 AI 분석을 다시 실행합니다.\n\n현재 분석 결과(요약·Action 등)는 새 결과로 바뀌고, 분석 확정은 해제됩니다. 녹취와 화자 정보는 그대로입니다.\n계속할까요?')) {
                void reanalyze(state.meeting, true)
              }
            }}
            reanalyzing={reanalyzing}
            reanalyzeError={reanalyzeError}
            onFinalize={() => finalize(state.meeting)}
            finalizing={finalizing}
            finalizeError={finalizeError}
            onNextMeeting={() => leaveTo(state.meeting, 'form')}
            onMeetingList={() => leaveTo(state.meeting, 'list')}
            onAdopt={(index, input) => adopt(state.meeting, index, input)}
          />
        )}
      </>
    )
  }

  return (
    <>
    <form id="meeting-form" className="panel meeting-form" onSubmit={submit} noValidate>
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
    <PreviousMeetings
      load={prevLoad}
      selected={previousIds}
      onChange={setPreviousIds}
      onDeleted={onDeleted}
      disabled={busy}
      highlightId={highlightId}
    />
    {previousIds.length === 1 && (
      <BriefingCard key={`${previousIds[0]}|${date}`} previousMeetingId={previousIds[0]} currentDate={date} />
    )}
    {previousIds.length > 1 && (
      <p className="muted small">지난 회의 브리핑은 이전 회의를 하나만 선택했을 때 들을 수 있습니다.</p>
    )}
    <Progress phase={phase} />
    </>
  )
}
