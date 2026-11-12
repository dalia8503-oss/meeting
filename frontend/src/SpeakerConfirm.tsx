import type { SpeakerList } from './api'
import type { SpeakerInput, SpeakerInputs } from './speakerInputs'

interface Props {
  list: SpeakerList
  inputs: SpeakerInputs
  onChange: (inputs: SpeakerInputs) => void
  disabled: boolean
}

/**
 * 분석 전 "화자 확인". STT가 구분한 speaker_N을 사람이 직접 확인한다.
 * 대표 발언은 실제 transcript 원문이다. 이름을 자동으로 채우거나 추천하지 않는다 (입력창은 비어 있다).
 */
export default function SpeakerConfirm({ list, inputs, onChange, disabled }: Props) {
  const set = (speaker: string, patch: Partial<SpeakerInput>) =>
    onChange({ ...inputs, [speaker]: { ...(inputs[speaker] ?? { name: '', unsure: false }), ...patch } })
  return (
    <section className="panel speaker-panel" aria-label="화자 확인">
      <h2 className="panel-title">화자 확인</h2>
      <p className="muted small">
        AI가 화자를 추측하지 않습니다. 각 화자가 누구인지 발언을 보고 직접 입력해 주세요. 모르면 “확실하지 않음”을 선택하면 미확인으로 분석합니다.
      </p>
      <ul className="speaker-list">
        {list.speakers.map((s) => {
          const i = inputs[s.speaker] ?? { name: '', unsure: false }
          return (
            <li key={s.speaker} className="speaker-item">
              <div className="speaker-label">
                {s.speaker} <span className="muted small">발언 {s.utterance_count}개</span>
              </div>
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
                  onChange={(e) => set(s.speaker, { name: e.target.value })}
                />
              </label>
              <label className="speaker-unsure small">
                <input
                  type="checkbox"
                  checked={i.unsure}
                  disabled={disabled}
                  onChange={(e) => set(s.speaker, { unsure: e.target.checked })}
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
