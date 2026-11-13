import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'
import MeetingForm from './MeetingForm'
import MinutesView from './MinutesView'

type Conn = { state: 'loading' } | { state: 'error' } | { state: 'ok'; health: Health }

export default function App() {
  const [conn, setConn] = useState<Conn>({ state: 'loading' })
  const [tab, setTab] = useState<'analyze' | 'minutes'>('analyze')

  useEffect(() => {
    fetchHealth()
      .then((health) => setConn({ state: 'ok', health }))
      .catch(() => setConn({ state: 'error' }))
  }, [])

  return (
    <main>
      <header className="top">
        <h1>MEETING ACTION AGENT</h1>
        <div className="badge-area">
          {conn.state === 'loading' && <span className="badge">연결 확인 중…</span>}
          {conn.state === 'error' && <span className="badge badge-error">Backend 연결 실패</span>}
          {conn.state === 'ok' && (
            <span className={`badge badge-${conn.health.mode}`}>
              ● {conn.health.mode.toUpperCase()}
            </span>
          )}
        </div>
      </header>
      <p className="tagline">회의에서 결정된 일을 완료될 때까지 추적합니다</p>
      <nav className="tabs" aria-label="화면 선택">
        <button type="button" className={`tab${tab === 'analyze' ? ' tab-on' : ''}`} aria-current={tab === 'analyze' ? 'page' : undefined}
          onClick={() => setTab('analyze')}>회의 분석</button>
        <button type="button" className={`tab${tab === 'minutes' ? ' tab-on' : ''}`} aria-current={tab === 'minutes' ? 'page' : undefined}
          onClick={() => setTab('minutes')}>회의록</button>
      </nav>
      {/* 두 화면 모두 마운트해 두고 숨긴다: 회의록을 보고 돌아와도 진행 중인 분석 화면 상태가 유지된다. */}
      <div hidden={tab !== 'analyze'}>
        <MeetingForm />
      </div>
      <div hidden={tab !== 'minutes'}>
        <MinutesView active={tab === 'minutes'} />
      </div>
    </main>
  )
}
