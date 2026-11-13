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
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
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

    if (p === '/api/meetings' && req.method === 'GET') {
      const list = [...meetings.values()]
        .map((m) => ({
          meeting_id: m.id, title: m.title, meeting_date: m.date, created_at: m.createdAt,
          analysis_status: m.analyzed ? 'analyzed' : 'uploaded', action_count: m.analyzed ? 1 : 0,
        }))
        .sort((a, b) => b.meeting_date.localeCompare(a.meeting_date) || b.created_at.localeCompare(a.created_at))
      return send(res, 200, { meetings: list })
    }

    const del = p.match(/^\/api\/meetings\/([^/]+)$/)
    if (del && req.method === 'DELETE') {
      if (!meetings.has(del[1])) return err(res, 404, 'meeting_not_found', '')
      if (meetings.get(del[1]).referenced) return err(res, 409, 'meeting_referenced', '')
      meetings.delete(del[1])
      return send(res, 200, { deleted: true, meeting_id: del[1] })
    }

    if (p === '/api/meetings' && req.method === 'POST') {
      const raw = await readBody(req)
      const title = field(raw, 'title')
      const date = field(raw, 'meeting_date')
      const id = `${date.replaceAll('-', '')}-${String(++seq).padStart(8, '0')}`
      meetings.set(id, { id, title, date, createdAt: now() })
      return send(res, 201, { meeting_id: id, title, meeting_date: date, audio_filename: 'demo.m4a', status: 'uploaded' })
    }

    // ---- 화자 확인 (T-LIVE-SPK): speaker_N 라벨만 주고, 이름은 사용자가 보낸 것만 저장한다
    const SPEAKERS = [
      { speaker: 'speaker_1', utterance_count: 3, samples: [{ timestamp: '00:00:05', text: '김대리님, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?' }] },
      { speaker: 'speaker_2', utterance_count: 2, samples: [{ timestamp: '00:00:10', text: '수정 완료해서 어제 공유했습니다.' }, { timestamp: '00:00:20', text: '네, 확인했습니다.' }] },
    ]
    // AI 1차 추천(추정값): 확정 speaker_map과 별개로만 보관한다.
    const SUGGESTIONS = [
      { speaker_id: 'speaker_1', suggested_name: null, confidence: 0, confidence_level: 'low', evidence: [], needs_confirmation: true },
      { speaker_id: 'speaker_2', suggested_name: '김대리', confidence: 0.9, confidence_level: 'high', needs_confirmation: false,
        evidence: ['speaker_1이 김대리를 직접 호명한 직후(00:00:10) 이 화자가 응답함'] },
    ]
    const isStale = (mt) => !!mt.analyzed && JSON.stringify(mt.usedMap ?? {}) !== JSON.stringify(mt.speakerMap ?? {})
    const speakerList = (mt) => ({
      meeting_id: mt.id, confirmed: !!mt.speakerMap, analyzed: !!mt.analyzed, stale: isStale(mt),
      speakers: SPEAKERS.map((s) => ({
        ...s, confirmed_name: mt.speakerMap?.[s.speaker] ?? null,
        confirmed_profile: mt.speakerProfiles?.[s.speaker]
          ? { ...mt.speakerProfiles[s.speaker], display_name: [mt.speakerProfiles[s.speaker].department, mt.speakerProfiles[s.speaker].section, mt.speakerProfiles[s.speaker].name].filter(Boolean).join(' ') }
          : null,
        suggestion: mt.suggested ? SUGGESTIONS.find((x) => x.speaker_id === s.speaker) : null,
      })),
    })
    const withLive = (mt, r) => ({
      ...r, stale: isStale(mt), speaker_map_used: mt.usedMap ?? {},
      analysis_finalized: !!mt.finalized && !isStale(mt), analysis_finalized_at: mt.finalized && !isStale(mt) ? mt.finalizedAt : null,
      transcript: [
        { timestamp: '00:00:05', speaker: 'speaker_1', text: '김대리님, 지난번 도면 수정해서 공유하는 건 어떻게 됐어요?' },
        { timestamp: '00:00:10', speaker: 'speaker_2', text: '수정 완료해서 어제 공유했습니다.' },
      ].map((t) => {
        const p = mt.speakerProfiles?.[t.speaker]
        return { ...t, speaker_name: p?.name ?? null, speaker_department: p?.department ?? null, speaker_section: p?.section ?? null,
          speaker_display: p ? [p.department, p.section, p.name].filter(Boolean).join(' ') : null }
      }),
    })
    let m = p.match(/^\/api\/meetings\/([^/]+)\/(transcribe|speakers|speaker-map|speaker-suggestions|reanalyze)$/)
    if (m) {
      const mt = meetings.get(m[1])
      if (!mt) return err(res, 404, 'meeting_not_found', '')
      if (m[2] === 'transcribe' && req.method === 'POST') {
        await new Promise((r) => setTimeout(r, 500))
        mt.transcribed = true
        return send(res, 200, speakerList(mt))
      }
      if (m[2] === 'reanalyze' && req.method === 'POST') {
        if (!mt.analyzed) return err(res, 409, 'not_analyzed', '')
        if (!isStale(mt)) return err(res, 409, 'not_stale', '')
        await new Promise((r) => setTimeout(r, 700))
        mt.usedMap = { ...(mt.speakerMap ?? {}) }
        return send(res, 200, withLive(mt, mt.lastResult))
      }
      if (!mt.transcribed) return err(res, 409, 'transcript_not_ready', '')
      if (m[2] === 'speaker-suggestions' && req.method === 'POST') {
        await new Promise((r) => setTimeout(r, 400))
        mt.suggested = true
        return send(res, 200, { meeting_id: mt.id, suggestions: SUGGESTIONS })
      }
      if (m[2] === 'speakers' && req.method === 'GET') return send(res, 200, speakerList(mt))
      if (m[2] === 'speaker-map' && req.method === 'PUT') {
        const body = JSON.parse((await readBody(req)) || '{}').speaker_map ?? {}
        const known = SPEAKERS.map((s) => s.speaker)
        for (const [k, v] of Object.entries(body)) {
          if (!known.includes(k)) return err(res, 422, 'invalid_speaker_map', `transcript에 없는 화자입니다: ${k}`)
          if (v !== null && !String(typeof v === 'object' ? v.name : v).trim()) return err(res, 422, 'invalid_speaker_map', `${k}: 이름이 비어 있습니다. 모르면 미확인으로 두세요.`)
        }
        const prof = (v) => (typeof v === 'object'
          ? { name: String(v.name).trim(), department: v.department?.trim() || null, section: v.section?.trim() || null }
          : { name: String(v).trim(), department: null, section: null })
        mt.finalized = false // 화자가 바뀌면 확정 해제 (실제 서버와 동일)
        mt.speakerProfiles = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null).map(([k, v]) => [k, prof(v)]))
        mt.speakerMap = Object.fromEntries(Object.entries(mt.speakerProfiles).map(([k, v]) => [k, v.name]))
        return send(res, 200, speakerList(mt))
      }
    }

    m = p.match(/^\/api\/meetings\/([^/]+)\/analyze$/)
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
      mt.usedMap = { ...(mt.speakerMap ?? {}) }
      const reply = (r) => { mt.lastResult = r; return send(res, 200, withLive(mt, r)) }
      const nm = (sp) => mt.speakerMap?.[sp] ?? null
      const ev = (ts, text, speaker) => ({ timestamp: ts, speaker, speaker_name: nm(speaker), text })
      const mk = (id, mtg, assignee, task, due, norm, e) => ({
        action_id: id, meeting_id: mtg, status: '신규', assignee, assigner: '팀장', task, due_date: due,
        due_date_normalized: norm, flags: [], evidence: e, approved_by_human: null, history: [],
      })
      const base = {
        meeting_id: mt.id, status: 'analyzed', mode: 'demo', utterance_count: 12,
        summary: [{ text: '지난주 진행 상황과 도면 작업 현황을 확인했다.', evidence_timestamps: ['00:00:08'] }],
        decisions: [], open_issues: [{ text: '다음 회의에서 품질 문제를 다룰지 제안됨 (결정되지 않음).', evidence_timestamps: ['00:00:20'] }],
        needs_confirmation: [{ text: '도면 검토가 끝났는지 질문이 나왔으나 답변이 없다.', evidence_timestamps: ['00:00:25'] }],
        rejected: [{ index: 0, assigner: '', reasons: ['허위담당자: 담당자 근거 없음'], adopted_action_ids: [], adopted_by: null, task: '품질 문제도 한번 보면 좋겠습니다', assignee: '', timestamp: '00:00:31', evidence: '다음에는 품질 문제도 한번 보면 좋겠습니다.' }],
        followup_invalid: [], new_actions_error: null, followup_error: null,
      }
      base.meeting_analysis = { summary: base.summary, decisions: base.decisions, open_issues: base.open_issues, needs_confirmation: base.needs_confirmation }
      if (prev.length === 0) {
        const a = mk('A-001', mt.id, '김대리', '도면 수정해서 공유', '금요일까지', null, ev('00:00:12', '김대리, 금요일까지 도면 수정해서 공유해주세요.', 'speaker_1'))
        actions.set(a.action_id, a)
        return reply({
          ...base, actions: [a], new_actions: [a], followup_results: [], previous_meeting_ids: [],
          execution: { mode: 'sequential', overlapped: false, reviewer: { PASS: 1 }, agents: [{ agent: 'meeting-analyst', status: 'ok', started_at: 0, finished_at: 1.2, reviewer: { PASS: 1 }, error: null }] },
        })
      }
      const a1 = actions.get('A-001')
      a1.status = '완료 후보'
      a1.history.push(hist('신규', '완료 후보', 'agent', { evidence: [ev('00:01:10', LONG, 'speaker_2')] }))
      const a2 = mk('A-002', mt.id, '박대리', '검토 의견 정리해서 공유', '다음주 화요일까지', '2026-11-24', ev('00:00:40', '박대리, 검토 의견 정리해서 다음주 화요일까지 공유해주세요.', 'speaker_1'))
      actions.set(a2.action_id, a2)
      return reply({
        ...base, actions: [a2, view(a1)], new_actions: [a2], previous_meeting_ids: prev,
        followup_results: [{ action_id: 'A-001', outcome: 'applied', from_status: '신규', to_status: '완료 후보', detail: '', reviewer_verdict: 'PASS', evidence: [ev('00:01:10', LONG, 'speaker_2'), ev('00:01:30', '네, 맞습니다.', 'speaker_1')] }],
        execution: {
          mode: 'parallel', overlapped: true, reviewer: { PASS: 2 },
          agents: [
            { agent: 'meeting-analyst', status: 'ok', started_at: 0, finished_at: 1.4, reviewer: { PASS: 1 }, error: null },
            { agent: 'followup-agent', status: 'ok', started_at: 0.02, finished_at: 1.1, reviewer: { PASS: 1 }, error: null },
          ],
        },
      })
    }

    m = p.match(/^\/api\/meetings\/([^/]+)\/finalize$/)
    if (m && req.method === 'POST') {
      const mt = meetings.get(m[1])
      if (!mt) return err(res, 404, 'meeting_not_found', '')
      if (!mt.lastResult) return err(res, 409, 'not_analyzed', '')
      if (isStale(mt)) return err(res, 409, 'analysis_stale', '')
      if (!mt.finalized) { mt.finalized = true; mt.finalizedAt = now() }
      return send(res, 200, withLive(mt, mt.lastResult))
    }

    m = p.match(/^\/api\/meetings\/([^/]+)\/result$/)
    if (m && req.method === 'GET') {
      const mt = meetings.get(m[1])
      if (!mt?.lastResult) return err(res, 404, 'not_analyzed', '')
      return send(res, 200, withLive(mt, mt.lastResult))
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
