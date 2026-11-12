// 화면 검증용 fixture API (Gemini/Claude 호출 없음, Backend 불필요).
//   node mock/server.mjs  →  http://localhost:8000  (VITE_API_BASE 기본값과 동일)
// 첫 회의(이전 회의 없음) → sequential, A-001 신규.
// 이전 회의를 선택한 회의 → parallel, A-001 완료 후보 + A-002 신규. 제목에 "conflict"가 있으면 409 snapshot conflict.
import http from 'node:http'

const PORT = Number(process.env.PORT ?? 8000)
const meetings = new Map()
const actions = new Map()
let seq = 0

const send = (res, status, body) => {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  })
  res.end(JSON.stringify(body))
}
const err = (res, status, code, message) => send(res, status, { detail: { code, message } })
const readBody = (req) =>
  new Promise((resolve) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')))
  })
const field = (raw, name) => raw.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1] ?? ''
const now = () => new Date().toISOString().replace('Z', '+00:00')
const hist = (from, to, actor, extra = {}) => ({ from, to, actor, at: now(), ...extra })
const view = (a) => ({ ...a, history: [...a.history] })

const LONG =
  '수정 완료해서 어제 공유했습니다. 도면 개정 이력과 검토 의견 반영 내역까지 모두 정리해서 공유 폴더 최상단에 올려두었고 관련 담당자 전원에게 메일로도 안내했습니다. '.repeat(2)

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x')
    const p = url.pathname
    if (req.method === 'OPTIONS') return send(res, 204, {})
    if (p === '/api/health') return send(res, 200, { status: 'ok', mode: 'demo', gemini_configured: false })

    if (p === '/api/meetings' && req.method === 'POST') {
      const raw = await readBody(req)
      const title = field(raw, 'title')
      const date = field(raw, 'meeting_date')
      const id = `${date.replaceAll('-', '')}-${String(++seq).padStart(8, '0')}`
      meetings.set(id, { id, title, date })
      return send(res, 201, { meeting_id: id, title, meeting_date: date, audio_filename: 'demo.m4a', status: 'uploaded' })
    }

    let m = p.match(/^\/api\/meetings\/([^/]+)\/analyze$/)
    if (m && req.method === 'POST') {
      const mt = meetings.get(m[1])
      if (!mt) return err(res, 404, 'meeting_not_found', '')
      if (mt.analyzed) return err(res, 409, 'already_analyzed', '')
      const body = JSON.parse((await readBody(req)) || '{}')
      const prev = body.previous_meeting_ids
      if (!Array.isArray(prev)) return err(res, 422, 'bad_request', 'previous_meeting_ids must be a list')
      if (prev.some((x) => !meetings.has(x))) return err(res, 404, 'previous_meeting_not_found', '')
      if (/conflict/i.test(mt.title)) return err(res, 409, 'followup_snapshot_conflict', '')
      await new Promise((r) => setTimeout(r, 900))
      mt.analyzed = true
      const ev = (ts, text, speaker) => ({ timestamp: ts, speaker, text })
      const mk = (id, mtg, assignee, task, due, norm, e) => ({
        action_id: id, meeting_id: mtg, status: '신규', assignee, assigner: '팀장', task, due_date: due,
        due_date_normalized: norm, flags: [], evidence: e, approved_by_human: null, history: [],
      })
      const base = {
        meeting_id: mt.id, status: 'analyzed', mode: 'demo', utterance_count: 12,
        summary: [{ text: '지난주 진행 상황과 도면 작업 현황을 확인했다.', evidence_timestamps: ['00:00:08'] }],
        decisions: [], open_issues: [{ text: '다음 회의에서 품질 문제를 다룰지 제안됨 (결정되지 않음).', evidence_timestamps: ['00:00:20'] }],
        needs_confirmation: [{ text: '도면 검토가 끝났는지 질문이 나왔으나 답변이 없다.', evidence_timestamps: ['00:00:25'] }],
        rejected: [{ task: '품질 문제도 한번 보면 좋겠습니다', assignee: '', timestamp: '00:00:31', evidence: '다음에는 품질 문제도 한번 보면 좋겠습니다.' }],
        followup_invalid: [], new_actions_error: null, followup_error: null,
      }
      base.meeting_analysis = { summary: base.summary, decisions: base.decisions, open_issues: base.open_issues, needs_confirmation: base.needs_confirmation }
      if (prev.length === 0) {
        const a = mk('A-001', mt.id, '김대리', '도면 수정해서 공유', '금요일까지', null, ev('00:00:12', '김대리, 금요일까지 도면 수정해서 공유해주세요.', '팀장'))
        actions.set(a.action_id, a)
        return send(res, 200, {
          ...base, actions: [a], new_actions: [a], followup_results: [], previous_meeting_ids: [],
          execution: { mode: 'sequential', overlapped: false, reviewer: { PASS: 1 }, agents: [{ agent: 'meeting-analyst', status: 'ok', started_at: 0, finished_at: 1.2, reviewer: { PASS: 1 }, error: null }] },
        })
      }
      const a1 = actions.get('A-001')
      a1.status = '완료 후보'
      a1.history.push(hist('신규', '완료 후보', 'agent', { evidence: [ev('00:01:10', LONG, '김대리')] }))
      const a2 = mk('A-002', mt.id, '박대리', '검토 의견 정리해서 공유', '다음주 화요일까지', '2026-11-24', ev('00:00:40', '박대리, 검토 의견 정리해서 다음주 화요일까지 공유해주세요.', '팀장'))
      actions.set(a2.action_id, a2)
      return send(res, 200, {
        ...base, actions: [a2, view(a1)], new_actions: [a2], previous_meeting_ids: prev,
        followup_results: [{ action_id: 'A-001', outcome: 'applied', from_status: '신규', to_status: '완료 후보', detail: '', reviewer_verdict: 'PASS', evidence: [ev('00:01:10', LONG, '김대리'), ev('00:01:30', '네, 맞습니다.', '팀장')] }],
        execution: {
          mode: 'parallel', overlapped: true, reviewer: { PASS: 2 },
          agents: [
            { agent: 'meeting-analyst', status: 'ok', started_at: 0, finished_at: 1.4, reviewer: { PASS: 1 }, error: null },
            { agent: 'followup-agent', status: 'ok', started_at: 0.02, finished_at: 1.1, reviewer: { PASS: 1 }, error: null },
          ],
        },
      })
    }

    m = p.match(/^\/api\/actions\/([^/]+)\/(review|withdraw-completion)$/)
    if (m && req.method === 'POST') {
      const a = actions.get(m[1])
      if (!a) return err(res, 404, 'action_not_found', '')
      const b = JSON.parse((await readBody(req)) || '{}')
      if (!b.actor_name?.trim()) return err(res, 422, 'invalid_review_request', '승인자(actor_name)은(는) 비어 있거나 공백일 수 없다')
      if (m[2] === 'withdraw-completion') {
        if (a.status !== '완료') return err(res, 409, 'invalid_transition', '')
        a.history.push(hist('완료', '진행중', b.actor_name))
        a.status = '진행중'
        return send(res, 200, view(a))
      }
      if (a.status !== '완료 후보') return err(res, 409, 'invalid_transition', '')
      const to = { 완료: '완료', '계속 진행': '진행중', '확인 필요': '확인 필요' }[b.choice]
      if (!to) return err(res, 422, 'invalid_review_request', '허용되지 않는 선택지')
      if (to === '완료') {
        if (!b.meeting_id || !b.timestamp) return err(res, 422, 'invalid_review_request', 'meeting_id는 비어 있을 수 없다')
        a.approved_by_human = { approver: b.actor_name, approved_at: new Date().toISOString().slice(0, 19) + '+09:00', evidence: { meeting_id: b.meeting_id, timestamp: b.timestamp } }
      }
      a.history.push(hist(a.status, to, b.actor_name))
      a.status = to
      return send(res, 200, view(a))
    }
    err(res, 404, 'not_found', '')
  })
  .listen(PORT, () => console.log(`mock API on http://localhost:${PORT}`))
