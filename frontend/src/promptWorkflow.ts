import type { AnalysisJob, PromptRevisionSummary } from './api'

export type PromptTask = 'generate' | 'refine' | 'optimize' | null

const active = (status: string) => ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(status)
const promptKind = (job: AnalysisJob) => job.kind === 'final_prompt_generation' || job.kind === 'prompt_refinement' || job.kind === 'prompt_selling_point_optimization'

export function choosePromptRevision(revisions: PromptRevisionSummary[], preferredVersion?: number) {
  return revisions.find((item) => item.version === preferredVersion) ?? revisions[0]
}

export function promptTaskFromJobs(jobs: AnalysisJob[]): PromptTask {
  const job = jobs.find((item) => promptKind(item) && active(item.status))
  if (job?.kind === 'prompt_refinement') return 'refine'
  if (job?.kind === 'prompt_selling_point_optimization') return 'optimize'
  if (job?.kind === 'final_prompt_generation') return 'generate'
  return null
}

export function latestPromptJob(jobs: AnalysisJob[]) {
  return jobs.find(promptKind)
}
