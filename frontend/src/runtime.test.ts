import { describe, expect, it, vi } from 'vitest'
import { localId } from './runtime'

describe('localId', () => {
  it('falls back when an older browser has no randomUUID', () => {
    vi.stubGlobal('crypto', {})
    expect(localId()).toMatch(/^local-/)
    vi.unstubAllGlobals()
  })
})
