import { analysisStatusLabel, statusTone } from '../workspaceDomain'

export function StatusBadge({ status }: { status?: string | null }) {
  return <span className={`status-badge ${statusTone(status)}`}><i />{analysisStatusLabel(status)}</span>
}
