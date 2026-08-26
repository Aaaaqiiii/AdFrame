import type { BatchStatus, GenerationBatch, GenerationSummary, PromptRevisionSummary } from './api'

/** 优先恢复当前提示词的批次；若提示词已换版，仍回退到最新历史批次，避免下载页永久失联。 */
export function chooseRestoredGenerationBatch(batches: GenerationBatch[], promptVersion?: number): GenerationBatch | null {
  return batches.find((batch) => batch.prompt_version === promptVersion) ?? batches[0] ?? null
}

/** 每个位置取最新 Generation.version（retry 后旧版本被新版本替代），并按位置 1..N 排序。 */
export function latestGenerationPerPosition(batch: GenerationBatch): GenerationSummary[] {
  const latest = new Map<number, GenerationSummary>()
  for (const generation of batch.generations) {
    const position = generation.batch_position ?? 0
    const existing = latest.get(position)
    if (!existing || (generation.version ?? 0) > (existing.version ?? 0)) {
      latest.set(position, generation)
    }
  }
  return [...latest.keys()].sort((a, b) => a - b).map((position) => latest.get(position)!)
}

/** 仅 queued/processing 视为活动批次（uncertain 需要人工解析，不算自动活动）。 */
export function isBatchActive(status: BatchStatus): boolean {
  return status === 'queued' || status === 'processing'
}

/** 只有 failed 可手动 retry；uncertain 走人工解析而非 retry。 */
export function canRetryGeneration(generation: GenerationSummary): boolean {
  return generation.status === 'failed'
}

/** 进度：completed/failed/submission_uncertain 计为完成；queued/processing/retryable 不计。 */
export function batchProgress(batch: GenerationBatch): { done: number; total: number } {
  const rows = latestGenerationPerPosition(batch)
  const done = rows.filter((g) => ['completed', 'failed', 'submission_uncertain'].includes(g.status)).length
  return { done, total: Math.max(batch.batch_size, rows.length) }
}

/** 单任务状态 → 中文标签。 */
export function generationStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: '排队中', processing: '生成中', retryable: '等待重试',
    completed: '已完成', failed: '失败', submission_uncertain: '提交状态不确定',
  }
  return labels[status] || '未知状态'
}

/** 区分本地素材发布与供应商生成，避免两个阶段都只显示“生成中”。 */
export function generationPhaseLabel(generation: GenerationSummary): string {
  if (generation.status === 'processing' && !generation.external_task_id) return '正在上传参考素材…'
  if (generation.status === 'retryable' && !generation.external_task_id) return '参考素材上传失败，等待自动重试'
  return generationStatusLabel(generation.status)
}

/** 仅 submission_uncertain 需要人工解析（而非 retry）。 */
export function canResolveUncertain(generation: GenerationSummary): boolean {
  return generation.status === 'submission_uncertain'
}

/** 批次状态 → 中文标签。 */
export function batchStatusLabel(status: BatchStatus): string {
  const labels: Record<BatchStatus, string> = {
    queued: '排队中', processing: '生成中', complete: '已完成', partial: '部分完成',
    failed: '已失败', uncertain: '需人工解析',
  }
  return labels[status] || '未知状态'
}

/** 选择最高版本的 completed full 提示词；忽略 queued/failed 与 legacy 模式。 */
export function chooseLatestFullPrompt(revisions: PromptRevisionSummary[]): PromptRevisionSummary | null {
  let chosen: PromptRevisionSummary | null = null
  for (const revision of revisions) {
    if (revision.status !== 'completed' || !revision.text) continue
    if (revision.prompt_mode !== 'full_reference_video_edit') continue
    if (revision.generation_segment_id != null) continue
    if (!chosen || revision.version > chosen.version) chosen = revision
  }
  return chosen
}
