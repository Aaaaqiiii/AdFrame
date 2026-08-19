import type { GenerationSegmentPlan, PromptRevisionSummary } from './api'

/** 批次创建前置判定：返回错误文案或 null（可创建）。 */
export function canCreateBatch(
  plan: GenerationSegmentPlan | null,
  prompt: PromptRevisionSummary | null,
  generationBusy: boolean,
): string | null {
  if (!plan || plan.plan_version <= 0 || !plan.segments.length) return '请先保存分段方案'
  if (!prompt || prompt.status !== 'completed' || prompt.prompt_mode !== 'full_reference_video_edit' || !prompt.text?.trim()) return '请先生成或选择一份完整提示词'
  if (generationBusy) return '已有生成任务进行中'
  return null
}

/** 批次创建前的任务数提示文案（用持久化方案段数）。 */
export function batchTaskCountLabel(plan: GenerationSegmentPlan | null): string {
  if (!plan || plan.plan_version <= 0 || !plan.segments.length) return '请先保存分段方案'
  return `将创建 ${plan.segments.length} 个生成任务，每段最多 ${Math.round(plan.max_segment_seconds)} 秒`
}
