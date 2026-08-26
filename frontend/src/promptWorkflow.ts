import type { AnalysisJob, PromptRevisionSummary } from './api'

export type PromptTask = 'generate' | 'refine' | 'optimize' | null

const active = (status: string) => ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(status)
const promptKind = (job: AnalysisJob) => job.kind === 'final_prompt_generation' || job.kind === 'prompt_refinement' || job.kind === 'prompt_selling_point_optimization'

export function promptJobKind(task: Exclude<PromptTask, null>) {
  return task === 'generate' ? 'final_prompt_generation' : task === 'optimize' ? 'prompt_selling_point_optimization' : 'prompt_refinement'
}

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

export function promptRetryNotice(job: AnalysisJob) {
  const attempts = job.attempts || 0
  const error = job.error_message || ''
  if (/proxy|connection|remote end|timed?\s*out|网络|连接/i.test(error)) {
    return `GPT 连接服务失败，正在自动重试（第 ${attempts} 次）…`
  }
  if (/缺少镜头|缺失镜头|仍不完整/i.test(error)) {
    return `GPT 正在补全缺失镜头，已自动重试 ${attempts} 次…`
  }
  return `GPT 请求失败，正在自动重试（第 ${attempts} 次）…`
}
