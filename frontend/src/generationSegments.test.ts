import { describe, expect, it } from 'vitest'
import { canSaveSegmentPlan, nearestShotBoundary, segmentDuration, segmentIssue } from './generationSegments'
import type { GenerationSegment } from './generationSegments'

const seg = (overrides: Partial<GenerationSegment> = {}): GenerationSegment => ({
  id: 's1',
  position: 0,
  source_start_sec: 0,
  source_end_sec: 16,
  start_boundary_type: 'video_edge',
  end_boundary_type: 'shot_boundary',
  short_segment_accepted: false,
  ...overrides,
})

describe('generation segment helpers', () => {
  it('computes segment duration', () => {
    expect(segmentDuration(seg())).toBe(16)
    expect(segmentDuration(seg({ source_start_sec: 8, source_end_sec: 32 }))).toBe(24)
  })

  it('accepts an original five second video as one segment', () => {
    expect(segmentIssue(seg({ source_start_sec: 0, source_end_sec: 5 }), 5, 29, 8)).toBeNull()
  })

  it('flags short split segment unless accepted', () => {
    expect(segmentIssue(seg({ source_start_sec: 0, source_end_sec: 6 }), 32, 29, 8)?.code).toBe('short_segment')
    expect(segmentIssue(seg({ source_start_sec: 0, source_end_sec: 6, short_segment_accepted: true }), 32, 29, 8)).toBeNull()
  })

  it('flags segment over the server limit', () => {
    expect(segmentIssue(seg({ source_start_sec: 0, source_end_sec: 30 }), 32, 29, 8)?.code).toBe('over_limit')
  })

  it('rejects gaps and segments over the server limit', () => {
    expect(canSaveSegmentPlan([{ source_start_sec: 0, source_end_sec: 30 }], 30, 29, 8)).toBe(false)
    expect(canSaveSegmentPlan([{ source_start_sec: 0, source_end_sec: 5 }], 5, 29, 8)).toBe(true)
    expect(canSaveSegmentPlan([{ source_start_sec: 0, source_end_sec: 16 }, { source_start_sec: 18, source_end_sec: 32 }], 32, 29, 8)).toBe(false)
  })

  it('snaps to the nearest shot boundary', () => {
    expect(nearestShotBoundary(15.7, [0, 8, 16, 24, 32])).toBe(16)
    expect(nearestShotBoundary(4, [0, 8, 16, 24, 32])).toBe(8)
    expect(nearestShotBoundary(0, [0, 8, 16, 24, 32])).toBe(0)
  })
})
