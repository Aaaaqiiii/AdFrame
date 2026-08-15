import { describe, expect, it } from 'vitest'

import { mergeAdjacentShots, moveSharedBoundary, splitAtPlayhead, timelineIsContinuous } from './timelineModel'

const shots = [
  { id: 'a', start: 0, end: 2 },
  { id: 'b', start: 2, end: 5 },
  { id: 'c', start: 5, end: 8 },
]

describe('human-authoritative timeline', () => {
  it('moves one selected boundary and preserves continuity', () => {
    const result = moveSharedBoundary(shots, 0, 3.125, 25)
    expect(result.map(({ start, end }) => [start, end])).toEqual([[0, 3.12], [3.12, 5], [5, 8]])
    expect(timelineIsContinuous(result)).toBe(true)
  })

  it('splits only at a playhead inside the selected shot', () => {
    expect(splitAtPlayhead(shots, 'b', 3.5)?.map(({ start, end }) => [start, end])).toEqual([
      [0, 2], [2, 3.52], [3.52, 5], [5, 8],
    ])
    expect(splitAtPlayhead(shots, 'b', 7)).toBeNull()
  })

  it('merges only two adjacent selected shots', () => {
    expect(mergeAdjacentShots(shots, ['a', 'b'])?.map(({ start, end }) => [start, end])).toEqual([[0, 5], [5, 8]])
    expect(mergeAdjacentShots(shots, ['a', 'c'])).toBeNull()
  })
})
