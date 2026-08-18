export type BoundaryType = 'video_edge' | 'shot_boundary' | 'inside_shot'

export type GenerationSegment = {
  id: string
  position: number
  source_start_sec: number
  source_end_sec: number
  start_boundary_type: BoundaryType
  end_boundary_type: BoundaryType
  short_segment_accepted: boolean
}

export type GenerationSegmentPlan = {
  plan_version: number
  timeline_revision_id: string | null
  max_segment_seconds: number
  recommended_min_seconds: number
  segments: GenerationSegment[]
}

export type GenerationSegmentInput = {
  source_start_sec: number
  source_end_sec: number
  start_boundary_type: BoundaryType
  end_boundary_type: BoundaryType
  short_segment_accepted: boolean
}

export type SegmentIssue = { code: 'over_limit' | 'short_segment'; message: string }

const EPSILON = 0.001

export function segmentDuration(segment: Pick<GenerationSegment, 'source_start_sec' | 'source_end_sec'>) {
  return segment.source_end_sec - segment.source_start_sec
}

export function segmentIssue(
  segment: Pick<GenerationSegment, 'source_start_sec' | 'source_end_sec'> & Partial<Pick<GenerationSegment, 'short_segment_accepted'>>,
  durationSec: number,
  maxSec: number,
  minSec: number,
): SegmentIssue | null {
  const duration = segmentDuration(segment)
  if (duration > maxSec + EPSILON) {
    return { code: 'over_limit', message: `片段超过 ${maxSec} 秒安全上限` }
  }
  const coversFullSource = segment.source_start_sec <= EPSILON && Math.abs(segment.source_end_sec - durationSec) <= EPSILON
  if (duration < minSec - EPSILON && !segment.short_segment_accepted && !coversFullSource) {
    return { code: 'short_segment', message: '短片段需要明确确认' }
  }
  return null
}

export function canSaveSegmentPlan(
  segments: Array<Pick<GenerationSegment, 'source_start_sec' | 'source_end_sec'> & Partial<Pick<GenerationSegment, 'short_segment_accepted'>>>,
  durationSec: number,
  maxSec: number,
  minSec: number,
): boolean {
  if (!segments.length) return false
  if (Math.abs(segments[0].source_start_sec) > EPSILON) return false
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index]
    if (segment.source_end_sec <= segment.source_start_sec) return false
    if (segmentIssue(segment, durationSec, maxSec, minSec)) return false
    if (index && Math.abs(segment.source_start_sec - segments[index - 1].source_end_sec) > EPSILON) return false
  }
  return Math.abs(segments[segments.length - 1].source_end_sec - durationSec) <= EPSILON
}

export function nearestShotBoundary(value: number, boundaries: number[]): number {
  const sorted = [...boundaries].sort((a, b) => a - b)
  if (!sorted.length) return value
  // 平手时取较大边界（升序遍历，后遇到的更大），吸附偏向切分右侧。
  let nearest = sorted[0]
  let bestDistance = Math.abs(value - sorted[0])
  for (const boundary of sorted) {
    const distance = Math.abs(value - boundary)
    if (distance <= bestDistance + EPSILON) {
      bestDistance = distance
      nearest = boundary
    }
  }
  return nearest
}
