import { describe, expect, it } from 'vitest'

import { timelineDuration } from './timelineMath'

describe('timeline duration', () => {
  it('uses the actual final shot end instead of an 18-second demo value', () => {
    expect(timelineDuration([{ end: 23.6 }, { end: 5.1 }])).toBe(23.6)
  })

  it('has no duration before analysis', () => {
    expect(timelineDuration([])).toBe(0)
  })
})
