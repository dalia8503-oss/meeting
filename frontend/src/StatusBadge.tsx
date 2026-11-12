const STATUS: Record<string, { icon: string; cls: string }> = {
  '신규': { icon: '🔵', cls: 'new' },
  '진행중': { icon: '🟠', cls: 'doing' },
  '완료 후보': { icon: '🟢', cls: 'cand' },
  '완료': { icon: '✅', cls: 'done' },
  '미완료': { icon: '🔴', cls: 'fail' },
  '확인 필요': { icon: '🟡', cls: 'confirm' },
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { icon: '⚪', cls: 'other' }
  return (
    <span className={`status status-${s.cls}`}>
      <span aria-hidden="true">{s.icon}</span> {status}
    </span>
  )
}
