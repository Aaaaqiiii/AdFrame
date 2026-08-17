import type { AnalysisJob, PromptRevisionSummary } from './api'

export type PromptTask = 'generate' | 'refine' | null

const active = (status: string) => ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(status)
const promptKind = (job: AnalysisJob) => job.kind === 'final_prompt_generation' || job.kind === 'prompt_refinement'

export function choosePromptRevision(revisions: PromptRevisionSummary[], preferredVersion?: number) {
  return revisions.find((item) => item.version === preferredVersion) ?? revisions[0]
}

export function promptTaskFromJobs(jobs: AnalysisJob[]): PromptTask {
  const job = jobs.find((item) => promptKind(item) && active(item.status))
  return job?.kind === 'prompt_refinement' ? 'refine' : job?.kind === 'final_prompt_generation' ? 'generate' : null
}

export function latestPromptJob(jobs: AnalysisJob[]) {
  return jobs.find(promptKind)
}
