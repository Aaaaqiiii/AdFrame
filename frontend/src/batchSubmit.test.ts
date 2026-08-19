import { describe, expect, it } from 'vitest'
import { batchTaskCountLabel, canCreateBatch } from './batchSubmit'
import type { GenerationSegmentPlan, PromptRevisionSummary } from './api'

const plan = (overrides: Partial<GenerationSegmentPlan> = {}): GenerationSegmentPlan => ({
  plan_version: 1,
  timeline_revision_id: 't1',
  max_segment_seconds: 29,
  recommended_min_seconds: 8,
  segments: [{ id: 's1', position: 0, source_start_sec: 0, source_end_sec: 16, start_boundary_type: 'video_edge', end_boundary_type: 'shot_boundary', short_segment_accepted: false }],
  ...overrides,
})

const prompt = (overrides: Partial<PromptRevisionSummary> = {}): PromptRevisionSummary => ({
  id: 'p1', version: 3, text: 'full prompt', status: 'completed',
  prompt_mode: 'full_reference_video_edit', generation_segment_id: null,
  source_timeline_revision_id: null, replace_product: false, replace_person: false, created_at: 'x',
  ...overrides,
})

describe('batch submit', () => {
  it('canCreateBatch requires saved plan, segments, selected prompt, not busy', () => {
    expect(canCreateBatch(plan(), prompt(), false)).toBeNull()
    expect(canCreateBatch(plan({ plan_version: 0 }), prompt(), false)).not.toBeNull()
    expect(canCreateBatch(plan({ segments: [] }), prompt(), false)).not.toBeNull()
    expect(canCreateBatch(plan(), prompt({ status: 'queued' }), false)).not.toBeNull()
    expect(canCreateBatch(plan(), prompt({ prompt_mode: 'reference_video_edit' }), false)).not.toBeNull()
    expect(canCreateBatch(plan(), prompt({ text: '' }), false)).not.toBeNull()
    expect(canCreateBatch(plan(), prompt(), true)).not.toBeNull()
  })

  it('batchTaskCountLabel uses persisted plan count', () => {
    expect(batchTaskCountLabel(plan({ segments: [{ id: 'a', position: 0, source_start_sec: 0, source_end_sec: 16, start_boundary_type: 'video_edge', end_boundary_type: 'shot_boundary', short_segment_accepted: false }, { id: 'b', position: 1, source_start_sec: 16, source_end_sec: 32, start_boundary_type: 'shot_boundary', end_boundary_type: 'video_edge', short_segment_accepted: false }] }))).toBe('将创建 2 个生成任务，每段最多 29 秒')
    expect(batchTaskCountLabel(plan({ plan_version: 0, segments: [] }))).toBe('请先保存分段方案')
  })
})
