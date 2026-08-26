import { analysisStatusLabel, statusTone } from '../workspaceDomain'

export function StatusBadge({ status, label }: { status?: string | null; label?: string }) {
  return <span className={`status-badge ${statusTone(status)}`}><i />{label || analysisStatusLabel(status)}</span>
}
