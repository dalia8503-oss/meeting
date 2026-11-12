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
