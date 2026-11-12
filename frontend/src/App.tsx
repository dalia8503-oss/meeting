import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'
import MeetingForm from './MeetingForm'

type Conn = { state: 'loading' } | { state: 'error' } | { state: 'ok'; health: Health }

export default function App() {
  const [conn, setConn] = useState<Conn>({ state: 'loading' })

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
      <p className="tagline">
        회의에서 결정된 일을
        <br />
        완료될 때까지 추적합니다.
      </p>
      <MeetingForm />
    </main>
  )
}
