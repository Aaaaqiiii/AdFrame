import type { ProjectMode } from './workspaceDomain'

const activeProjectKey = 'adflow.active-project-id'

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

export function saveProjectId(storage: StorageLike, projectId: string, mode?: ProjectMode) {
  storage.setItem(mode ? `${activeProjectKey}.${mode}` : activeProjectKey, projectId)
}

export function restoreProjectId(storage: StorageLike, mode?: ProjectMode) {
  return storage.getItem(mode ? `${activeProjectKey}.${mode}` : activeProjectKey)
}
