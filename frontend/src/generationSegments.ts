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

export function boundaryTypeAt(value: number, shotBoundaries: number[], durationSec: number): BoundaryType {
  // 视频边缘优先；其次真实分镜边界；都不是则为镜头内部。
  if (Math.abs(value) <= EPSILON || Math.abs(value - durationSec) <= EPSILON) return 'video_edge'
  if (shotBoundaries.some((boundary) => Math.abs(value - boundary) <= EPSILON)) return 'shot_boundary'
  return 'inside_shot'
}

export function splitSegment(
  segments: GenerationSegmentInput[],
  targetIndex: number,
  at: number,
  shotBoundaries: number[],
  durationSec: number,
  allowInsideShot: boolean,
): GenerationSegmentInput[] {
  if (targetIndex < 0 || targetIndex >= segments.length) return segments
  const target = segments[targetIndex]
  if (at <= target.source_start_sec + EPSILON || at >= target.source_end_sec - EPSILON) return segments
  let cut = at
  let cutType: BoundaryType = 'shot_boundary'
  if (allowInsideShot) {
    cutType = boundaryTypeAt(at, shotBoundaries, durationSec)
  } else {
    const candidates = shotBoundaries.filter((b) => b > target.source_start_sec + EPSILON && b < target.source_end_sec - EPSILON)
    if (!candidates.length) return segments  // 无合法分镜边界，不允许伪标 shot_boundary。
    const snapped = nearestShotBoundary(at, candidates)
    if (snapped <= target.source_start_sec || snapped >= target.source_end_sec) return segments
    cut = snapped
  }
  const next = [...segments]
  // 分割改变了左段右边界与右段左边界，两段都需重新确认短段。
  next.splice(targetIndex, 1,
    { ...target, source_end_sec: cut, end_boundary_type: cutType, short_segment_accepted: false },
    { ...target, source_start_sec: cut, start_boundary_type: cutType, short_segment_accepted: false },
  )
  return next
}

export function mergeSegments(
  segments: GenerationSegmentInput[],
  index: number,
  durationSec: number,
): GenerationSegmentInput[] {
  // 合并 index 与 index+1 两段，保留两端外侧边界类型；内部切点消失。
  if (index < 0 || index + 1 >= segments.length) return segments
  const left = segments[index]
  const right = segments[index + 1]
  const merged: GenerationSegmentInput = {
    source_start_sec: left.source_start_sec,
    source_end_sec: right.source_end_sec,
    start_boundary_type: left.source_start_sec <= EPSILON ? 'video_edge' : left.start_boundary_type,
    end_boundary_type: Math.abs(right.source_end_sec - durationSec) <= EPSILON ? 'video_edge' : right.end_boundary_type,
    // 合并产生新边界，短段确认必须重新确认。
    short_segment_accepted: false,
  }
  const next = [...segments]
  next.splice(index, 2, merged)
  return next
}

export function moveSegmentBoundary(
  segments: GenerationSegmentInput[],
  index: number,
  requested: number,
  shotBoundaries: number[],
  durationSec: number,
  allowInsideShot: boolean,
): GenerationSegmentInput[] {
  if (index < 0 || index + 1 >= segments.length) return segments
  const current = segments[index]
  const neighbor = segments[index + 1]
  let cut = requested
  let cutType: BoundaryType = 'shot_boundary'
  if (allowInsideShot) {
    cutType = boundaryTypeAt(requested, shotBoundaries, durationSec)
  } else {
    const candidates = shotBoundaries.filter((b) => b > current.source_start_sec + EPSILON && b < neighbor.source_end_sec - EPSILON)
    if (!candidates.length) return segments  // 无合法分镜边界，不允许伪标 shot_boundary。
    const snapped = nearestShotBoundary(requested, candidates)
    if (snapped <= current.source_start_sec || snapped >= neighbor.source_end_sec) return segments
    cut = snapped
  }
  if (cut <= current.source_start_sec + EPSILON || cut >= neighbor.source_end_sec - EPSILON) return segments
  const next = [...segments]
  // 边界移动改变了相邻两段，短段确认都需重新确认。
  next[index] = { ...current, source_end_sec: cut, end_boundary_type: cutType, short_segment_accepted: false }
  next[index + 1] = { ...neighbor, source_start_sec: cut, start_boundary_type: cutType, short_segment_accepted: false }
  return next
}

export function toggleShortSegmentAccepted(
  segments: GenerationSegmentInput[],
  index: number,
): GenerationSegmentInput[] {
  if (index < 0 || index >= segments.length) return segments
  const next = [...segments]
  next[index] = { ...next[index], short_segment_accepted: !next[index].short_segment_accepted }
  return next
}

export function shouldResetDraft(planIdentity: number, lastIdentity: number | null): boolean {
  // 只在稳定方案身份变化时重置草稿；同一不可变方案的重复 GET 不重置。
  return lastIdentity === null || lastIdentity !== planIdentity
}

export function isShortSegmentCandidate(
  segment: Pick<GenerationSegmentInput, 'source_start_sec' | 'source_end_sec'>,
  durationSec: number,
  minSec: number,
): boolean {
  // 实际时长小于推荐下限且不是完整短视频 → 持续显示确认控件（与确认状态无关）。
  const duration = segmentDuration(segment)
  if (duration >= minSec - EPSILON) return false
  const coversFullSource = segment.source_start_sec <= EPSILON && Math.abs(segment.source_end_sec - durationSec) <= EPSILON
  return !coversFullSource
}

export function segmentStructureMatches(
  draft: GenerationSegmentInput[],
  persisted: GenerationSegment[],
): boolean {
  if (draft.length !== persisted.length) return false
  return draft.every((segment, index) => {
    const other = persisted[index]
    return Boolean(other)
      && Math.abs(segment.source_start_sec - other.source_start_sec) <= EPSILON
      && Math.abs(segment.source_end_sec - other.source_end_sec) <= EPSILON
  })
}
