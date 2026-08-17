import { describe, expect, it } from 'vitest'
import type { PromptRevisionSummary } from './api'
import { choosePromptRevision, latestPromptJob, promptTaskFromJobs } from './promptWorkflow'

const revisions: PromptRevisionSummary[] = [
  { id: 'p3', version: 3, text: 'third', status: 'completed', source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T03:00:00Z' },
  { id: 'p1', version: 1, text: 'first', status: 'completed', source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T01:00:00Z' },
]

describe('prompt-only recovery', () => {
  it('keeps a selected completed version and otherwise chooses the latest', () => {
    expect(choosePromptRevision(revisions, 1)?.text).toBe('first')
    expect(choosePromptRevision(revisions, 99)?.version).toBe(3)
    expect(choosePromptRevision([])).toBeUndefined()
  })

  it('restores an active refinement or generation task', () => {
    expect(promptTaskFromJobs([{ job_id: 'r', kind: 'prompt_refinement', status: 'retryable' }])).toBe('refine')
    expect(promptTaskFromJobs([{ job_id: 'g', kind: 'final_prompt_generation', status: 'queued' }])).toBe('generate')
    expect(promptTaskFromJobs([{ job_id: 'f', kind: 'prompt_refinement', status: 'failed' }])).toBeNull()
    expect(promptTaskFromJobs([{ job_id: 'other', kind: 'vision_analysis', status: 'running' }])).toBeNull()
  })

  it('returns the newest prompt job, even when it is terminal', () => {
    expect(latestPromptJob([
      { job_id: 'new', kind: 'prompt_refinement', status: 'failed', error_message: 'bad' },
      { job_id: 'old', kind: 'final_prompt_generation', status: 'completed' },
    ])?.job_id).toBe('new')
    expect(latestPromptJob([{ job_id: 'other', kind: 'vision_analysis', status: 'running' }])).toBeUndefined()
  })
})
