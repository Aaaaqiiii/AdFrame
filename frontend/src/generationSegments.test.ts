import { describe, expect, it } from 'vitest'
import {
  boundaryTypeAt,
  canSaveSegmentPlan,
  isShortSegmentCandidate,
  mergeSegments,
  moveSegmentBoundary,
  nearestShotBoundary,
  segmentDuration,
  segmentIssue,
  segmentStructureMatches,
  shouldResetDraft,
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

  it('shouldResetDraft only fires when plan identity changes', () => {
    expect(shouldResetDraft(2, null)).toBe(true)  // 首次加载
    expect(shouldResetDraft(2, 2)).toBe(false)   // 同一 plan_version 重复 GET 不重置
    expect(shouldResetDraft(3, 2)).toBe(true)    // 方案版本变化重置
  })

  it('split and move reset short-segment acceptance on changed segments', () => {
    const base = [input({ source_start_sec: 0, source_end_sec: 16, short_segment_accepted: true, end_boundary_type: 'shot_boundary' }), input({ source_start_sec: 16, source_end_sec: 32, end_boundary_type: 'video_edge', start_boundary_type: 'shot_boundary' })]
    const split = splitSegment(base, 0, 8, [0, 8, 16, 32], 32, false)
    expect(split[0].short_segment_accepted).toBe(false)
    expect(split[1].short_segment_accepted).toBe(false)
    const moved = moveSegmentBoundary(base, 0, 24, [0, 8, 16, 24, 32], 32, false)
    expect(moved[0].short_segment_accepted).toBe(false)
    expect(moved[1].short_segment_accepted).toBe(false)
    const merged = mergeSegments(base, 0, 32)
    expect(merged[0].short_segment_accepted).toBe(false)
  })

  it('isShortSegmentCandidate stays visible after acceptance', () => {
    const short = input({ source_start_sec: 0, source_end_sec: 6, short_segment_accepted: true })
    // 即使已确认，短段仍是候选，复选框保持显示可取消。
    expect(isShortSegmentCandidate(short, 32, 8)).toBe(true)
    // 完整短视频不是候选。
    expect(isShortSegmentCandidate(input({ source_start_sec: 0, source_end_sec: 5 }), 5, 8)).toBe(false)
    // 达到推荐下限不是候选。
    expect(isShortSegmentCandidate(input({ source_start_sec: 0, source_end_sec: 8 }), 32, 8)).toBe(false)
  })

  it('segmentStructureMatches detects draft divergence', () => {
    const persisted: GenerationSegment[] = [
      { id: 's1', position: 0, source_start_sec: 0, source_end_sec: 16, start_boundary_type: 'video_edge', end_boundary_type: 'shot_boundary', short_segment_accepted: false },
      { id: 's2', position: 1, source_start_sec: 16, source_end_sec: 32, start_boundary_type: 'shot_boundary', end_boundary_type: 'video_edge', short_segment_accepted: false },
    ]
    expect(segmentStructureMatches(twoSegments(), persisted)).toBe(true)
    // 分割后 draft 变三段 → 结构不一致（不能按旧索引选择后端 segment）。
    const split = splitSegment(twoSegments(), 0, 8, [0, 8, 16, 32], 32, false)
    expect(segmentStructureMatches(split, persisted)).toBe(false)
  })
})
