import { describe, expect, it } from 'vitest'

import { restoreProjectId, restoreWorkflowStage, saveProjectId, saveWorkflowStage } from './projectSession'

function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  }
}

describe('project session', () => {
  it('restores the active project after a page refresh', () => {
    const storage = memoryStorage()
    saveProjectId(storage, 'project-123')

    expect(restoreProjectId(storage)).toBe('project-123')
  })

  it('restores the active workflow step after a page refresh', () => {
    const storage = memoryStorage()
    saveWorkflowStage(storage, 'prompt', 'replace_product')

    expect(restoreWorkflowStage(storage, 'replace_product')).toBe('prompt')
    expect(restoreWorkflowStage(storage, 'preserve_product')).toBeNull()
  })
})
