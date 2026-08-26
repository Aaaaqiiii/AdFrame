import type { ProjectMode, WorkflowStage } from './workspaceDomain'

const activeProjectKey = 'adflow.active-project-id'
const activeStageKey = 'adflow.active-stage'

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

export function saveProjectId(storage: StorageLike, projectId: string, mode?: ProjectMode) {
  storage.setItem(mode ? `${activeProjectKey}.${mode}` : activeProjectKey, projectId)
}

export function restoreProjectId(storage: StorageLike, mode?: ProjectMode) {
  return storage.getItem(mode ? `${activeProjectKey}.${mode}` : activeProjectKey)
}

export function saveWorkflowStage(storage: StorageLike, stage: WorkflowStage, mode: ProjectMode) {
  storage.setItem(`${activeStageKey}.${mode}`, stage)
}

export function restoreWorkflowStage(storage: StorageLike, mode: ProjectMode): WorkflowStage | null {
  const value = storage.getItem(`${activeStageKey}.${mode}`)
  return ['materials', 'analysis', 'timeline', 'segments', 'shots', 'prompt', 'generation'].includes(value || '')
    ? value as WorkflowStage
    : null
}
