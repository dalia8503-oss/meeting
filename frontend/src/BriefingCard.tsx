import { useEffect, useRef, useState } from 'react'
import { briefingAudioUrl, createBriefing, type Briefing } from './api'

interface Props {
  previousMeetingId: string
  /** 지금 시작하려는 회의의 meeting_date (브라우저 오늘 날짜가 아님) */
  currentDate: string
}

type State =
  | { s: 'idle' }
  | { s: 'loading' }
  | { s: 'ready'; briefing: Briefing }
  | { s: 'error'; message: string }

/**
 * 지난 회의 브리핑. 자동 재생 없음: 음성은 사용자가 ▶를 누를 때만 나온다.
 * 생성(POST)은 한 번만, 이후 다시 듣기는 audio.play()만 한다 (LLM 재호출 없음).
 * 부모가 key={previousMeetingId|currentDate}로 마운트하므로 회의/날짜가 바뀌면 상태가 통째로 초기화된다.
 */
export default function BriefingCard({ previousMeetingId, currentDate }: Props) {
  const [state, setState] = useState<State>({ s: 'idle' })
  const [playing, setPlaying] = useState(false)
  const [playError, setPlayError] = useState(false)
  const [played, setPlayed] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    const el = audio.current
    return () => {
      alive.current = false
      el?.pause()
    }
  }, [])

  const generate = async () => {
    setState({ s: 'loading' })
    try {
      const briefing = await createBriefing(previousMeetingId, currentDate)
      if (alive.current) setState({ s: 'ready', briefing }) // 응답 후에도 자동 재생하지 않는다
    } catch (err) {
      if (alive.current) {
        setState({ s: 'error', message: err instanceof Error ? err.message : '브리핑을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.' })
      }
    }
  }

  const play = () => {
    setPlayError(false)
    audio.current?.play().catch(() => {
      setPlaying(false)
      setPlayError(true)
    })
  }

  const stop = () => {
    const el = audio.current
    if (!el) return
    el.pause()
    el.currentTime = 0
    setPlaying(false)
  }

  return (
    <section className="panel briefing-card" aria-label="지난 회의 브리핑">
      <h2 className="panel-title">🔊 지난 회의 브리핑</h2>
      {state.s === 'idle' && (
        <>
          <p className="muted small">지난 회의 핵심과 남아 있는 업무를 확인합니다.</p>
          <div className="actions">
            <button type="button" className="btn-secondary" onClick={generate}>▶ 지난 회의 브리핑 듣기</button>
          </div>
        </>
      )}
      {state.s === 'loading' && (
        <p className="muted" role="status"><span className="spinner" aria-hidden="true" /> 지난 회의를 정리하고 있습니다...</p>
      )}
      {state.s === 'error' && (
        <>
          <p className="error" role="alert">{state.message}</p>
          <div className="actions">
            <button type="button" className="btn-secondary" onClick={generate}>다시 시도</button>
          </div>
        </>
      )}
      {state.s === 'ready' && (
        <>
          <p className="briefing-text">{state.briefing.briefing_text}</p>
          <audio
            ref={audio}
            src={briefingAudioUrl(state.briefing.audio)}
            preload="metadata"
            onPlay={() => { setPlaying(true); setPlayed(true) }}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onError={() => { setPlaying(false); setPlayError(true) }}
          />
          <div className="briefing-controls">
            {playing ? (
              <>
                <button type="button" className="btn-secondary" onClick={stop}>■ 정지</button>
                <span className="muted small" role="status">🔊 재생 중...</span>
              </>
            ) : (
              <button type="button" className="btn-secondary" onClick={play}>{played ? '▶ 다시 듣기' : '▶ 듣기'}</button>
            )}
            <span className="muted small">약 {Math.round(state.briefing.audio.duration_sec)}초</span>
          </div>
          {playError && <p className="error small" role="alert">음성을 재생하지 못했습니다. 위 텍스트로 확인해 주세요.</p>}
        </>
      )}
    </section>
  )
}
