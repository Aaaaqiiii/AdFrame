import { describe, expect, it } from 'vitest'
import type { PromptRevisionSummary } from './api'
import { choosePromptRevision, latestPromptJob, promptJobKind, promptRetryNotice, promptTaskFromJobs } from './promptWorkflow'

const revisions: PromptRevisionSummary[] = [
  { id: 'p3', version: 3, text: 'third', status: 'completed', prompt_mode: 'full_reference_video_edit', generation_segment_id: null, source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T03:00:00Z' },
  { id: 'p1', version: 1, text: 'first', status: 'completed', prompt_mode: 'full_reference_video_edit', generation_segment_id: null, source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T01:00:00Z' },
]

describe('prompt-only recovery', () => {
  it('keeps a selected completed version and otherwise chooses the latest', () => {
    expect(choosePromptRevision(revisions, 1)?.text).toBe('first')
    expect(choosePromptRevision(revisions, 99)?.version).toBe(3)
    expect(choosePromptRevision([])).toBeUndefined()
  })

  it('restores an active refinement, generation, or optimization task', () => {
    expect(promptTaskFromJobs([{ job_id: 'o', kind: 'prompt_selling_point_optimization', status: 'queued' }])).toBe('optimize')
    expect(promptTaskFromJobs([{ job_id: 'r', kind: 'prompt_refinement', status: 'retryable' }])).toBe('refine')
    expect(promptTaskFromJobs([{ job_id: 'g', kind: 'final_prompt_generation', status: 'queued' }])).toBe('generate')
    expect(promptTaskFromJobs([{ job_id: 'f', kind: 'prompt_refinement', status: 'failed' }])).toBeNull()
    expect(promptTaskFromJobs([{ job_id: 'other', kind: 'vision_analysis', status: 'running' }])).toBeNull()
    expect(promptTaskFromJobs([
      { job_id: 'done', kind: 'prompt_refinement', status: 'completed' },
      { job_id: 'active', kind: 'final_prompt_generation', status: 'processing' },
    ])).toBe('generate')
  })

  it('returns the newest prompt job, even when it is terminal', () => {
    expect(latestPromptJob([
      { job_id: 'new', kind: 'prompt_refinement', status: 'failed', error_message: 'bad' },
      { job_id: 'old', kind: 'final_prompt_generation', status: 'completed' },
    ])?.job_id).toBe('new')
    expect(latestPromptJob([{ job_id: 'other', kind: 'vision_analysis', status: 'running' }])).toBeUndefined()
  })

  it('maps every prompt task to the job kind used by polling', () => {
    expect(promptJobKind('generate')).toBe('final_prompt_generation')
    expect(promptJobKind('refine')).toBe('prompt_refinement')
    expect(promptJobKind('optimize')).toBe('prompt_selling_point_optimization')
  })

  it('reports the real retry category instead of always claiming shots are missing', () => {
    expect(promptRetryNotice({ job_id: 'network', status: 'retryable', attempts: 1, error_message: 'ProxyError: Remote end closed connection' }))
      .toBe('GPT 连接服务失败，正在自动重试（第 1 次）…')
    expect(promptRetryNotice({ job_id: 'missing', status: 'retryable', attempts: 2, error_message: 'GPT 返回的提示词仍不完整，缺少镜头：00:01-00:02' }))
      .toBe('GPT 正在补全缺失镜头，已自动重试 2 次…')
  })
})
