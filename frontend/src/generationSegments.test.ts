import { describe, expect, it } from 'vitest'
import {
  boundaryTypeAt,
  canSaveSegmentPlan,
  mergeSegments,
  moveSegmentBoundary,
  nearestShotBoundary,
  segmentDuration,
  segmentIssue,
  splitSegment,
  toggleShortSegmentAccepted,
} from './generationSegments'
import type { GenerationSegment, GenerationSegmentInput } from './generationSegments'

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

  const input = (overrides: Partial<GenerationSegmentInput> = {}): GenerationSegmentInput => ({
    source_start_sec: 0,
    source_end_sec: 16,
    start_boundary_type: 'video_edge',
    end_boundary_type: 'shot_boundary',
    short_segment_accepted: false,
    ...overrides,
  })
  const twoSegments = (): GenerationSegmentInput[] => [
    input({ source_end_sec: 16, end_boundary_type: 'shot_boundary' }),
    input({ source_start_sec: 16, start_boundary_type: 'shot_boundary', source_end_sec: 32, end_boundary_type: 'video_edge' }),
  ]
  const boundaries = [0, 8, 16, 24, 32]

  it('mergeSegments joins adjacent segments preserving outer boundary types', () => {
    const merged = mergeSegments(twoSegments(), 0, 32)
    expect(merged).toHaveLength(1)
    expect(merged[0].source_start_sec).toBe(0)
    expect(merged[0].source_end_sec).toBe(32)
    expect(merged[0].start_boundary_type).toBe('video_edge')
    expect(merged[0].end_boundary_type).toBe('video_edge')
  })

  it('splitSegment snaps to shot boundary when inside shot is disallowed', () => {
    const split = splitSegment([input({ source_end_sec: 32, end_boundary_type: 'video_edge' })], 0, 12.3, boundaries, 32, false)
    expect(split).toHaveLength(2)
    expect(split[0].source_end_sec).toBe(16) // 12.3 吸附到最近的 16
    expect(split[0].end_boundary_type).toBe('shot_boundary')
    expect(split[1].source_start_sec).toBe(16)
  })

  it('splitSegment allows inside-shot boundary and types it correctly', () => {
    const split = splitSegment([input({ source_end_sec: 35, end_boundary_type: 'video_edge' })], 0, 17.5, [0, 35], 35, true)
    expect(split[0].end_boundary_type).toBe('inside_shot')
    expect(split[1].start_boundary_type).toBe('inside_shot')
  })

  it('splitSegment never fabricates shot_boundary when no legal boundary exists', () => {
    // 无内部分镜边界且不允许内部切分时，拒绝切分。
    const split = splitSegment([input({ source_end_sec: 35, end_boundary_type: 'video_edge' })], 0, 17.5, [0, 35], 35, false)
    expect(split).toHaveLength(1)
  })

  it('moveSegmentBoundary moves and re-types a shared boundary', () => {
    const moved = moveSegmentBoundary(twoSegments(), 0, 24, boundaries, 32, false)
    expect(moved[0].source_end_sec).toBe(24)
    expect(moved[0].end_boundary_type).toBe('shot_boundary')
    expect(moved[1].source_start_sec).toBe(24)
  })

  it('toggleShortSegmentAccepted flips acceptance', () => {
    const toggled = toggleShortSegmentAccepted(twoSegments(), 1)
    expect(toggled[1].short_segment_accepted).toBe(true)
    expect(toggled[0].short_segment_accepted).toBe(false)
  })

  it('boundaryTypeAt classifies video edge, shot boundary and inside shot', () => {
    expect(boundaryTypeAt(0, boundaries, 32)).toBe('video_edge')
    expect(boundaryTypeAt(32, boundaries, 32)).toBe('video_edge')
    expect(boundaryTypeAt(16, boundaries, 32)).toBe('shot_boundary')
    expect(boundaryTypeAt(12.3, boundaries, 32)).toBe('inside_shot')
  })
})
