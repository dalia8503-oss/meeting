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

/** 사용자가 명시적으로 누른 회의 삭제. 서버가 성공을 돌려준 뒤에만 호출 측이 목록에서 제거한다. */
export async function deleteMeeting(meetingId: string): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/meetings/${encodeURIComponent(meetingId)}`, { method: 'DELETE' })
  } catch {
    throw new Error('Backend에 연결할 수 없습니다. 서버가 실행 중인지 확인해 주세요.')
  }
  if (res.ok) return
  let code = ''
  try {
    code = (await res.json())?.detail?.code ?? ''
  } catch {
    /* ignore */
  }
  const messages: Record<string, string> = {
    meeting_referenced: '이 회의의 업무가 다른 회의에서 이미 추적되고 있어 삭제할 수 없습니다.',
    meeting_busy: '분석 중인 회의는 삭제할 수 없습니다.',
    meeting_not_found: '이미 삭제되었거나 찾을 수 없는 회의입니다.',
  }
  throw new Error(messages[code] ?? '회의를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.')
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
  /** 부/과를 포함한 표시명 (예: "1부 3과 이과장"). 표시 전용 */
  speaker_display?: string | null
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
  /** 담당자·지시자가 확인된 화자 이름과 일치할 때만 부/과 포함 표시명 (원문 assignee는 그대로) */
  assignee_display?: string | null
  assigner_display?: string | null
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
  assigner: string
  timestamp: string
  evidence: string
  /** 채택 API가 가리키는 순번 */
  index: number
  /** Reviewer가 제외한 사유 */
  reasons: string[]
  /** 사람이 채택해 만들어진 Action (없으면 미채택) */
  adopted_action_ids: string[]
  adopted_by: string | null
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
  /** 원본 transcript 줄 (읽기 전용). speaker는 원본 라벨, speaker_name은 사람이 확인한 이름 */
  transcript: TranscriptLine[]
  /** 분석 이후 화자 확인이 바뀌어 이 결과가 최신이 아님 → 재분석 필요 */
  stale: boolean
  speaker_map_used: Record<string, string>
  /** 사람이 "회의 분석 확정"을 눌렀다 (서버가 meta.json에 저장). stale이면 항상 false. 화면 흐름 표시 전용이며 Action 상태와 무관하다. */
  analysis_finalized?: boolean
  analysis_finalized_at?: string | null
}

export interface TranscriptLine {
  timestamp: string
  speaker: string
  speaker_name: string | null
  speaker_display?: string | null
  speaker_department?: string | null
  speaker_section?: string | null
  text: string
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
  gemini_busy: 'Gemini가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요.',
  rejected_not_found: '제외된 후보를 찾을 수 없습니다. 화면을 새로고침해 주세요.',
  already_adopted: '이미 Action으로 채택한 후보입니다.',
  already_registered: '같은 담당자의 같은 업무가 이미 Action으로 등록되어 있습니다.',
  invalid_adoption: '입력값을 확인해 주세요. (채택자 이름이 필요하거나, 이 후보를 Action으로 변환할 수 없습니다.)',
  assignee_required: '담당자가 화자 라벨이거나 확인되지 않은 후보입니다. 담당자를 직접 지정해 주세요.',
  analysis_stale: '화자 정보가 변경되어 재분석이 필요한 결과입니다. 먼저 변경된 화자로 다시 분석해 주세요.',
  not_stale: '화자 변경이 없어 재분석이 필요하지 않습니다.',
  reanalysis_blocked: '이 회의의 분석 결과가 다른 회의 또는 사람의 검토에 이미 사용되어 자동으로 다시 분석할 수 없습니다. 기존 결과는 변경되지 않았습니다.',
  speaker_suggestion_invalid: '화자 추천 결과가 검증을 통과하지 못했습니다. 직접 입력해 주세요.',
  speaker_suggestion_failed: '화자 추천에 실패했습니다. 직접 입력해 주세요.',
  // 브리핑: REJECT 초안은 서버가 주지 않는다. 사용자에게는 안전 문구만 보여 준다.
  briefing_rejected: '안전하게 확인되지 않은 브리핑이라 재생하지 않았습니다.',
  briefing_review_failed: '안전하게 확인되지 않은 브리핑이라 재생하지 않았습니다.',
  previous_meeting_not_analyzed: '선택한 이전 회의가 아직 분석되지 않았습니다.',
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
  /** 사람이 확인한 이름 (speaker_map). 확정값이다 */
  confirmed_name: string | null
  /** AI 1차 추천. 추정값이며 사람이 확인하기 전에는 확정 신원이 아니다 */
  suggestion?: SpeakerSuggestion | null
  confirmed_profile?: SpeakerProfile | null
}

export interface SpeakerProfile {
  name: string
  department: string | null
  section: string | null
  display_name: string
}

/** 저장 요청 한 화자: 이름 문자열(구형) 또는 부/과/이름. null = 미확인 */
export type SpeakerMapValue = string | { name: string; department?: string | null; section?: string | null } | null

export type ConfidenceLevel = 'high' | 'medium' | 'low'

export interface SpeakerSuggestion {
  speaker_id: string
  suggested_name: string | null
  /** 녹취에 이름과 함께 명시된 경우에만 채워진다 */
  department?: string | null
  section?: string | null
  confidence: number
  confidence_level: ConfidenceLevel
  evidence: string[]
  needs_confirmation: boolean
}

export interface SpeakerList {
  meeting_id: string
  speakers: SpeakerItem[]
  confirmed: boolean
  analyzed?: boolean
  stale?: boolean
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
export function saveSpeakerMap(meetingId: string, speakerMap: Record<string, SpeakerMapValue>): Promise<SpeakerList> {
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

export interface BriefingAudio {
  format: 'wav'
  media_type: string
  duration_sec: number
  size_bytes: number
  /** 서버 상대 경로 (/api/briefings/{id}/audio). briefingAudioUrl()로 절대 URL을 만든다. */
  url: string
}

export interface Briefing {
  status: 'approved'
  briefing_id: string
  previous_meeting_id: string
  current_date: string
  briefing_text: string
  referenced_action_ids: string[]
  reviewer_verdict: 'PASS'
  audio: BriefingAudio
  /** 개발/debug 정보. 사용자 화면에는 표시하지 않는다. */
  reused: boolean
}

/** 지난 회의 브리핑 생성(또는 저장본 조회). regenerate는 항상 false다 (MVP UI에 새로 만들기 없음). */
export function createBriefing(previousMeetingId: string, currentDate: string): Promise<Briefing> {
  return call<Briefing>(
    '/api/briefings',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ previous_meeting_id: previousMeetingId, current_date: currentDate, regenerate: false }),
    },
    '브리핑을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

export function briefingAudioUrl(audio: BriefingAudio): string {
  return `${API_BASE}${audio.url}`
}

/** Speaker Identification Agent의 1차 추천을 만든다. 추천은 저장만 되며 확정 화자(speaker_map)는 바뀌지 않는다. */
export function suggestSpeakers(meetingId: string): Promise<{ meeting_id: string; suggestions: SpeakerSuggestion[] }> {
  return call(
    `/api/meetings/${encodeURIComponent(meetingId)}/speaker-suggestions`,
    { method: 'POST' },
    '화자 추천에 실패했습니다. 직접 입력해 주세요.',
  )
}

/** 화자가 바뀌어 stale이 된 분석을 사용자가 명시적으로 다시 실행한다. */
export function reanalyzeMeeting(meetingId: string): Promise<AnalysisResult> {
  return call<AnalysisResult>(
    `/api/meetings/${encodeURIComponent(meetingId)}/reanalyze`,
    { method: 'POST' },
    '재분석에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

/** 사람이 분석 결과를 확정한다. Action 0건이어도 가능하며 Action·Reviewer 판정은 바뀌지 않는다. */
export function finalizeAnalysis(meetingId: string): Promise<AnalysisResult> {
  return call<AnalysisResult>(
    `/api/meetings/${encodeURIComponent(meetingId)}/finalize`,
    { method: 'POST' },
    '회의 분석을 확정하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  )
}

/** 저장된 분석 결과를 다시 읽는다 (화자 표시명 갱신용). */
export function fetchResult(meetingId: string): Promise<AnalysisResult> {
  return call<AnalysisResult>(
    `/api/meetings/${encodeURIComponent(meetingId)}/result`,
    { method: 'GET' },
    '분석 결과를 불러오지 못했습니다.',
  )
}

/** Reviewer가 제외한 후보를 사람이 Action으로 채택한다 (Reviewer 판정은 바뀌지 않는다). 갱신된 분석 결과를 돌려준다. */
export function adoptRejected(
  meetingId: string,
  index: number,
  body: { actorName: string; assignees: string[]; assigner: string },
): Promise<AnalysisResult> {
  return call<AnalysisResult>(
    `/api/meetings/${encodeURIComponent(meetingId)}/rejected/${index}/adopt`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_name: body.actorName,
        assignees: body.assignees.length ? body.assignees : null,
        assigner: body.assigner || null,
      }),
    },
    'Action으로 채택하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  )
}
