export type Mode = 'live' | 'demo'

export interface Health {
  status: 'ok'
  mode: Mode
  gemini_configured: boolean
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export async function fetchHealth(): Promise<Health> {
  const res = await fetch(`${API_BASE}/api/health`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as Health
}

export interface MeetingCreated {
  meeting_id: string
  title: string
  meeting_date: string
  audio_filename: string
  status: 'uploaded'
}

export interface MeetingSummary {
  meeting_id: string
  title: string
  meeting_date: string
  created_at: string | null
  analysis_status: 'uploaded' | 'analyzed'
  action_count: number
}

/** 서버에 저장된 회의 목록 (이전 회의 후보). 실패하면 호출 측이 오류 상태로 처리한다. */
export async function fetchMeetings(): Promise<MeetingSummary[]> {
  const res = await fetch(`${API_BASE}/api/meetings`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = (await res.json()) as { meetings?: MeetingSummary[] }
  if (!Array.isArray(body.meetings)) throw new Error('invalid response')
  return body.meetings
}

const ERROR_MESSAGES: Record<string, string> = {
  missing_title: '회의명을 입력해 주세요.',
  missing_meeting_date: '회의일을 입력해 주세요.',
  invalid_meeting_date: '회의일 형식이 올바르지 않습니다.',
  missing_file: '회의 녹음 파일을 선택해 주세요.',
  invalid_audio: '지원하지 않거나 손상된 파일입니다. MP3 / WAV / M4A만 가능합니다.',
  duplicate_meeting: '이미 등록된 회의입니다. (같은 날짜·같은 녹음 파일)',
  file_too_large: '파일이 너무 큽니다.',
}
const GENERIC_ERROR = '회의 등록에 실패했습니다. 잠시 후 다시 시도해 주세요.'

/** 서버 메시지를 그대로 쓰지 않고 code만 사용자 문구로 변환한다. */
export async function createMeeting(form: {
  title: string
  meetingDate: string
  file: File
}): Promise<MeetingCreated> {
  const body = new FormData()
  body.append('title', form.title)
  body.append('meeting_date', form.meetingDate)
  body.append('audio_file', form.file)
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/meetings`, { method: 'POST', body })
  } catch {
    throw new Error('Backend에 연결할 수 없습니다. 서버가 실행 중인지 확인해 주세요.')
  }
  if (!res.ok) {
    let code = ''
    try {
      code = (await res.json())?.detail?.code ?? ''
    } catch {
      /* ignore */
    }
    throw new Error(ERROR_MESSAGES[code] ?? GENERIC_ERROR)
  }
  return (await res.json()) as MeetingCreated
}

export interface SummaryItem {
  text: string
  evidence_timestamps: string[]
}

export interface Evidence {
  timestamp: string
  /** 원본 STT 화자 라벨(speaker_N). 이름으로 바꾸지 않는다. */
  speaker: string | null
  /** 사용자가 확인한 이름 (보조 표시). 미확인이면 null/없음 */
  speaker_name?: string | null
  text: string
}

/** 사람 승인 기록. 값은 서버가 준 그대로 표시한다. */
export interface ApprovedByHuman {
  approver?: string
  approved_at?: string
  evidence?: { meeting_id?: string; timestamp?: string }
}

export interface HistoryEntry {
  [key: string]: unknown
}

export interface ActionView {
  action_id: string
  meeting_id: string | null
  status: string
  assignee: string
  assigner: string
  task: string
  due_date: string | null
  due_date_normalized: string | null
  flags: string[]
  evidence: Evidence
  approved_by_human: ApprovedByHuman | null
  history: HistoryEntry[]
}

export type FollowupOutcome = 'applied' | 'reassessed' | 'rejected' | 'skipped'

export interface FollowupResult {
  action_id: string
  outcome: FollowupOutcome | string
  from_status: string | null
  to_status: string | null
  detail: string
  reviewer_verdict: string | null
  evidence: Evidence[]
}

export interface AgentRun {
  agent: string
  status: 'ok' | 'error' | string
  started_at: number
  finished_at: number
  reviewer: Record<string, number>
  error: string | null
}

export interface Execution {
  mode: 'sequential' | 'parallel'
  overlapped: boolean
  agents: AgentRun[]
  reviewer: Record<string, number>
}

export interface RejectedItem {
  task: string
  assignee: string
  timestamp: string
  evidence: string
}

export interface AnalysisResult {
  meeting_id: string
  status: 'analyzed'
  mode: Mode
  utterance_count: number
  summary: SummaryItem[]
  decisions: SummaryItem[]
  open_issues: SummaryItem[]
  needs_confirmation: SummaryItem[]
  actions: ActionView[]
  rejected: RejectedItem[]
  meeting_analysis: {
    summary: SummaryItem[]
    decisions: SummaryItem[]
    open_issues: SummaryItem[]
    needs_confirmation: SummaryItem[]
  } | null
  new_actions: ActionView[]
  followup_results: FollowupResult[]
  followup_invalid: Record<string, unknown>[]
  previous_meeting_ids: string[]
  execution: Execution | null
  new_actions_error: string | null
  followup_error: string | null
}

export type ReviewChoice = '완료' | '계속 진행' | '확인 필요'

const NETWORK_ERROR = 'Backend에 연결할 수 없습니다. 서버가 실행 중인지 확인해 주세요.'

/** code → 사용자 문구. raw JSON은 보여주지 않는다. */
const API_ERRORS: Record<string, string> = {
  meeting_not_found: '회의를 찾을 수 없습니다.',
  previous_meeting_not_found: '선택한 이전 회의를 찾을 수 없습니다. 목록을 확인해 주세요.',
  invalid_previous_meeting: '현재 회의를 이전 회의로 지정할 수 없습니다.',
  not_analyzed: '아직 분석되지 않은 회의입니다.',
  already_analyzed: '이미 분석된 회의입니다.',
  analysis_in_progress: '이 회의는 분석이 진행 중입니다.',
  gemini_not_configured: 'LIVE 모드에 필요한 Gemini API 키가 서버에 설정되어 있지 않습니다.',
  stt_failed: '음성 변환(STT)에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  analysis_invalid: '회의 분석 결과가 검증을 통과하지 못했습니다. 다시 시도해 주세요.',
  review_failed: 'Reviewer 검증에 실패했습니다. 다시 시도해 주세요.',
  audio_integrity_error: '원본 녹음이 변경된 것으로 보여 분석을 중단했습니다.',
  followup_snapshot_conflict: '이미 이 회의의 이행점검 결과가 저장되어 있습니다. 기존 결과를 덮어쓰지 않습니다.',
  completion_guard_violation: '완료 승인 조건을 만족하지 않아 처리하지 못했습니다. 완료는 사람이 새 근거로 승인해야 합니다.',
  invalid_transition: '현재 상태에서는 허용되지 않는 처리입니다. 화면의 상태가 오래되었을 수 있습니다.',
  action_not_found: 'Action을 찾을 수 없습니다.',
  transcript_not_ready: '아직 음성 변환(STT)이 되지 않은 회의입니다.',
}

class ApiFailure extends Error {}

async function call<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, init)
  } catch {
    throw new ApiFailure(NETWORK_ERROR)
  }
  if (res.ok) return (await res.json()) as T
  let code = ''
  let message = ''
  try {
    const d = (await res.json())?.detail
    // FastAPI 본문 검증 오류(422)는 detail이 배열이다 → 코드/메시지 없음으로 처리한다.
    code = typeof d?.code === 'string' ? d.code : ''
    message = typeof d?.message === 'string' ? d.message : ''
  } catch {
    /* ignore */
  }
  if (API_ERRORS[code]) throw new ApiFailure(API_ERRORS[code])
  // 검증 안내문(invalid_review_request)은 서버가 사용자용 한국어 문구를 준다. 실제 원인을 숨기지 않는다.
  if ((code === 'invalid_review_request' || code === 'invalid_speaker_map') && message) throw new ApiFailure(message)
  throw new ApiFailure(fallback)
}

/**
 * 동기 요청이다. 서버가 진행률을 주지 않으므로 호출 측은 "분석 중"만 표시한다.
 * previousMeetingIds는 항상 배열로 보낸다. 이전 회의가 없으면 [] (null은 서버에서 다른 의미가 될 수 있어 쓰지 않는다).
 */
export function analyzeMeeting(meetingId: string, previousMeetingIds: string[]): Promise<AnalysisResult> {
  return call<AnalysisResult>(
    `/api/meetings/${encodeURIComponent(meetingId)}/analyze`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ previous_meeting_ids: previousMeetingIds }),
    },
    '회의 분석에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

export interface SpeakerItem {
  speaker: string
  utterance_count: number
  /** transcript의 실제 발언 원문 (요약·생성 아님) */
  samples: { timestamp: string; text: string }[]
  confirmed_name: string | null
}

export interface SpeakerList {
  meeting_id: string
  speakers: SpeakerItem[]
  confirmed: boolean
}

/** STT 준비 단계: 원본 transcript(speaker_N)와 화자 목록만 만든다. 분석은 하지 않는다. */
export function transcribeMeeting(meetingId: string): Promise<SpeakerList> {
  return call<SpeakerList>(
    `/api/meetings/${encodeURIComponent(meetingId)}/transcribe`,
    { method: 'POST' },
    '음성 변환에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

/** 사용자가 확인한 화자 이름 저장. null = 미확인. 회의별로만 저장되며 다른 회의에는 적용되지 않는다. */
export function saveSpeakerMap(meetingId: string, speakerMap: Record<string, string | null>): Promise<SpeakerList> {
  return call<SpeakerList>(
    `/api/meetings/${encodeURIComponent(meetingId)}/speaker-map`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speaker_map: speakerMap }) },
    '화자 확인 저장에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

export function reviewAction(
  actionId: string,
  body: { choice: ReviewChoice; actor_name: string; meeting_id?: string; timestamp?: string },
): Promise<ActionView> {
  return call<ActionView>(
    `/api/actions/${encodeURIComponent(actionId)}/review`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
    '처리에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

export function withdrawCompletion(actionId: string, actorName: string): Promise<ActionView> {
  return call<ActionView>(
    `/api/actions/${encodeURIComponent(actionId)}/withdraw-completion`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ actor_name: actorName }) },
    '완료 철회에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}
