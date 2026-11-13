import type { SpeakerList } from './api'
import { CONFIDENCE_LABEL, displayName, type SpeakerInput, type SpeakerInputs } from './speakerInputs'

interface Props {
  list: SpeakerList
  inputs: SpeakerInputs
  onChange: (inputs: SpeakerInputs) => void
  disabled: boolean
  /** AI 추천을 (다시) 받는 중인지 / 추천 요청 / 추천 실패 안내 */
  suggesting?: boolean
  onSuggest?: () => void
  suggestError?: string
}

/**
 * 분석 전 "화자 확인". AI(Speaker Identification Agent)가 녹취 근거로 1차 추천한 이름은 추정값이다.
 * 사람이 확인하거나 수정해야만 확정 화자(speaker_map)가 된다. 어떤 추천도 자동으로 확정되지 않는다.
 */
export default function SpeakerConfirm({ list, inputs, onChange, disabled, suggesting, onSuggest, suggestError }: Props) {
  const blank: SpeakerInput = { name: '', unsure: false, reviewed: true }
  const set = (speaker: string, patch: Partial<SpeakerInput>) =>
    onChange({ ...inputs, [speaker]: { ...(inputs[speaker] ?? blank), ...patch } })
  return (
    <section className="panel speaker-panel" aria-label="화자 확인">
      <div className="panel-head">
        <h2 className="panel-title">화자 확인</h2>
        {onSuggest && (
          <button type="button" className="btn-link" disabled={disabled || suggesting} onClick={onSuggest}>
            {suggesting ? '추천 중…' : 'AI 추천 다시 받기'}
          </button>
        )}
      </div>
      <p className="muted small">
        AI 추천은 녹취 속 호명·대화 관계에 근거한 <strong>추정</strong>이며 확정 신원이 아닙니다. 각 화자를 확인하거나 수정해야 분석에 사용됩니다.
        모르면 “확실하지 않음”을 선택하면 미확인으로 분석합니다.
      </p>
      {suggestError && <p className="error small" role="alert">{suggestError}</p>}
      <ul className="speaker-list">
        {list.speakers.map((s) => {
          const i = inputs[s.speaker] ?? blank
          const sg = s.suggestion
          const pending = !i.reviewed
          return (
            <li key={s.speaker} className="speaker-item">
              <div className="speaker-label">
                {s.speaker} <span className="muted small">발언 {s.utterance_count}개</span>
                <span className={`speaker-state ${pending ? 'speaker-state-pending' : 'speaker-state-ok'}`}>
                  {pending ? '확인 필요' : i.unsure || !i.name.trim() ? '미확인' : '확인됨'}
                </span>
              </div>
              {sg?.suggested_name ? (
                <div className="speaker-ai small">
                  AI 추천: <strong>{[sg.department, sg.section, sg.suggested_name].filter(Boolean).join(' ')}</strong>{' '}
                  <span className={`conf conf-${sg.confidence_level}`}>신뢰도 {CONFIDENCE_LABEL[sg.confidence_level]}</span>
                  {sg.needs_confirmation && <span className="flag">확인 필요</span>}
                  {sg.evidence.length > 0 && (
                    <ul className="speaker-evidence">
                      {sg.evidence.map((e, k) => (
                        <li key={k}>근거: {e}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                sg && <div className="speaker-ai muted small">AI 추천 없음 (녹취에서 이름을 특정할 근거가 부족합니다)</div>
              )}
              <ul className="speaker-samples">
                {s.samples.map((q) => (
                  <li key={q.timestamp}>
                    <span className="muted small">{q.timestamp}</span> “{q.text}”
                  </li>
                ))}
              </ul>
              <label className="speaker-input">
                이 화자는 누구인가요?
                <input
                  value={i.unsure ? '' : i.name}
                  placeholder="이름 또는 역할 (예: 김대리)"
                  disabled={disabled || i.unsure}
                  onChange={(e) => set(s.speaker, { name: e.target.value, reviewed: true })}
                />
              </label>
              <div className="speaker-org">
                <label className="speaker-input">
                  소속 부 (선택)
                  <input value={i.department ?? ''} placeholder="예: 1부" disabled={disabled || i.unsure}
                    onChange={(e) => set(s.speaker, { department: e.target.value, reviewed: true })} />
                </label>
                <label className="speaker-input">
                  소속 과 (선택)
                  <input value={i.section ?? ''} placeholder="예: 3과" disabled={disabled || i.unsure}
                    onChange={(e) => set(s.speaker, { section: e.target.value, reviewed: true })} />
                </label>
              </div>
              {!i.unsure && i.name.trim() && (
                <div className="small">최종 표시: <strong>{displayName(i)}</strong></div>
              )}
              {pending && (
                <button type="button" className="btn-secondary" disabled={disabled}
                  onClick={() => set(s.speaker, { reviewed: true })}>
                  화자 확인
                </button>
              )}
              <label className="speaker-unsure small">
                <input
                  type="checkbox"
                  checked={i.unsure}
                  disabled={disabled}
                  onChange={(e) => set(s.speaker, { unsure: e.target.checked, reviewed: true })}
                />
                누가 말했는지 확실하지 않음
              </label>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
