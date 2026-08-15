import { describe, expect, it } from 'vitest'
import { moveBoundary } from './timelineEditing'

describe('manual timeline editing', () => {
  it('moves one shared boundary without creating a gap or overlap', () => {
    expect(moveBoundary([{ start: 0, end: 2 }, { start: 2, end: 5 }], 0, 3)).toEqual([
      { start: 0, end: 3 }, { start: 3, end: 5 },
    ])
  })

  it('keeps at least one frame on each side', () => {
    expect(moveBoundary([{ start: 0, end: 2 }, { start: 2, end: 5 }], 0, 0)).toEqual([
      { start: 0, end: 0.04 }, { start: 0.04, end: 5 },
    ])
  })
})
