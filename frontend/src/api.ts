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

export interface ActionView {
  action_id: string
  status: string
  assignee: string
  assigner: string
  task: string
  due_date: string | null
  due_date_normalized: string | null
  flags: string[]
  evidence: { timestamp: string; speaker: string | null; text: string }
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
  rejected: { task: string; assignee: string; timestamp: string; evidence: string }[]
}

const ANALYZE_ERRORS: Record<string, string> = {
  meeting_not_found: '회의를 찾을 수 없습니다.',
  already_analyzed: '이미 분석된 회의입니다.',
  analysis_in_progress: '이 회의는 분석이 진행 중입니다.',
  gemini_not_configured: 'LIVE 모드에 필요한 Gemini API 키가 서버에 설정되어 있지 않습니다.',
  stt_failed: '음성 변환(STT)에 실패했습니다. 잠시 후 다시 시도해 주세요.',
  analysis_invalid: '회의 분석 결과가 검증을 통과하지 못했습니다. 다시 시도해 주세요.',
  review_failed: 'Reviewer 검증에 실패했습니다. 다시 시도해 주세요.',
  audio_integrity_error: '원본 녹음이 변경된 것으로 보여 분석을 중단했습니다.',
}

/** 동기 요청이다. 서버가 진행률을 주지 않으므로 호출 측은 "분석 중"만 표시한다. */
export async function analyzeMeeting(meetingId: string): Promise<AnalysisResult> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/meetings/${encodeURIComponent(meetingId)}/analyze`, { method: 'POST' })
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
    throw new Error(ANALYZE_ERRORS[code] ?? '회의 분석에 실패했습니다. 잠시 후 다시 시도해 주세요.')
  }
  return (await res.json()) as AnalysisResult
}
