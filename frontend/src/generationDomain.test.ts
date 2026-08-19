import { describe, expect, it } from 'vitest'
import { batchProgress, batchStatusLabel, canResolveUncertain, canRetryGeneration, chooseLatestFullPrompt, generationStatusLabel, isBatchActive, latestGenerationPerPosition } from './generationDomain'
import type { GenerationBatch, GenerationSummary, PromptRevisionSummary } from './api'

const gen = (overrides: Partial<GenerationSummary> = {}): GenerationSummary => ({
  id: 'g1', version: 1, status: 'queued', provider: 'volcengine', ...overrides,
})

describe('generation domain', () => {
  it('latestGenerationPerPosition replaces retried versions per position', () => {
    const batch: GenerationBatch = {
      generation_batch_id: 'b1', project_id: 'p1', provider: 'volcengine',
      prompt_version: 1, batch_size: 2, status: 'processing',
      generations: [
        gen({ id: 'a', version: 2, status: 'completed', batch_position: 1 }),
        gen({ id: 'b', version: 4, status: 'failed', batch_position: 2 }),
        gen({ id: 'c', version: 6, status: 'queued', batch_position: 2 }), // retry v6
      ],
    }
    const latest = latestGenerationPerPosition(batch)
    expect(latest).toHaveLength(2)
    expect(latest[0].id).toBe('a')
    expect(latest[1].id).toBe('c') // v6 替代 v4
  })

  it('orders by position even when input is shuffled', () => {
    const batch: GenerationBatch = {
      generation_batch_id: 'b1', project_id: 'p1', provider: 'volcengine',
      prompt_version: 1, batch_size: 3, status: 'processing',
      generations: [
        gen({ id: 'c', version: 1, status: 'queued', batch_position: 3 }),
        gen({ id: 'a', version: 1, status: 'queued', batch_position: 1 }),
        gen({ id: 'b', version: 1, status: 'queued', batch_position: 2 }),
      ],
    }
    expect(latestGenerationPerPosition(batch).map((g) => g.batch_position)).toEqual([1, 2, 3])
  })

  it('missing positions are not counted as complete', () => {
    const batch: GenerationBatch = {
      generation_batch_id: 'b1', project_id: 'p1', provider: 'volcengine',
      prompt_version: 1, batch_size: 3, status: 'processing',
      generations: [gen({ id: 'a', version: 1, status: 'completed', batch_position: 1 })],
    }
    const latest = latestGenerationPerPosition(batch)
    expect(latest).toHaveLength(1)
    expect(batchProgress(batch)).toEqual({ done: 1, total: 3 })
  })

  it('active is true only for queued and processing', () => {
    expect(isBatchActive('queued')).toBe(true)
    expect(isBatchActive('processing')).toBe(true)
    expect(isBatchActive('complete')).toBe(false)
    expect(isBatchActive('partial')).toBe(false)
    expect(isBatchActive('failed')).toBe(false)
    expect(isBatchActive('uncertain')).toBe(false)
  })

  it('uncertain is not auto-retryable', () => {
    expect(canRetryGeneration(gen({ status: 'failed' }))).toBe(true)
    expect(canRetryGeneration(gen({ status: 'submission_uncertain' }))).toBe(false)
    expect(canRetryGeneration(gen({ status: 'queued' }))).toBe(false)
    expect(canRetryGeneration(gen({ status: 'completed' }))).toBe(false)
  })

  it('progress counts completed, failed, uncertain as done', () => {
    const batch: GenerationBatch = {
      generation_batch_id: 'b1', project_id: 'p1', provider: 'volcengine',
      prompt_version: 1, batch_size: 4, status: 'partial',
      generations: [
        gen({ id: 'a', status: 'completed', batch_position: 1 }),
        gen({ id: 'b', status: 'failed', batch_position: 2 }),
        gen({ id: 'c', status: 'submission_uncertain', batch_position: 3 }),
        gen({ id: 'd', status: 'queued', batch_position: 4 }),
      ],
    }
    expect(batchProgress(batch)).toEqual({ done: 3, total: 4 })
  })

  it('chooseLatestFullPrompt ignores queued/failed and legacy modes', () => {
    const revisions: PromptRevisionSummary[] = [
      { id: 'r1', version: 3, text: 'v3', status: 'queued', prompt_mode: 'full_reference_video_edit', generation_segment_id: null, source_timeline_revision_id: null, replace_product: false, replace_person: false, created_at: '2026-01-01' },
      { id: 'r2', version: 2, text: 'v2', status: 'completed', prompt_mode: 'reference_video_edit', generation_segment_id: 'seg1', source_timeline_revision_id: null, replace_product: false, replace_person: false, created_at: '2026-01-01' },
      { id: 'r3', version: 1, text: 'v1', status: 'completed', prompt_mode: 'full_reference_video_edit', generation_segment_id: null, source_timeline_revision_id: null, replace_product: false, replace_person: false, created_at: '2026-01-01' },
    ]
    const chosen = chooseLatestFullPrompt(revisions)
    expect(chosen?.version).toBe(1) // v3 queued 忽略，v2 legacy 忽略，v1 full completed
  })
})

describe('generation stage domain', () => {
  it('maps generation statuses to labels', () => {
    expect(generationStatusLabel('queued')).toBe('排队中')
    expect(generationStatusLabel('processing')).toBe('生成中')
    expect(generationStatusLabel('retryable')).toBe('等待重试')
    expect(generationStatusLabel('completed')).toBe('已完成')
    expect(generationStatusLabel('failed')).toBe('失败')
    expect(generationStatusLabel('submission_uncertain')).toBe('提交状态不确定')
    expect(generationStatusLabel('unknown')).toBe('未知状态')
  })

  it('uncertain generation needs manual resolve, not retry', () => {
    expect(canResolveUncertain(gen({ status: 'submission_uncertain' }))).toBe(true)
    expect(canResolveUncertain(gen({ status: 'failed' }))).toBe(false)
  })

  it('batch status labels', () => {
    expect(batchStatusLabel('queued')).toBe('排队中')
    expect(batchStatusLabel('processing')).toBe('生成中')
    expect(batchStatusLabel('complete')).toBe('已完成')
    expect(batchStatusLabel('partial')).toBe('部分完成')
    expect(batchStatusLabel('failed')).toBe('已失败')
    expect(batchStatusLabel('uncertain')).toBe('需人工解析')
  })
})
