import { describe, expect, it } from 'vitest'
import { inferProductProfile } from './productProfile'

describe('product profile', () => {
  it('deduplicates AI observations into an editable starting point', () => {
    expect(inferProductProfile([
      { productInteraction: '手持白色瓶身' },
      { productInteraction: '手持白色瓶身' },
      { observations: '蓝色瓶盖，正面 Logo' },
    ])).toBe('手持白色瓶身；蓝色瓶盖，正面 Logo')
  })

  it('does not invent a fallback when AI found no product facts', () => {
    expect(inferProductProfile([])).toBe('')
  })
})
