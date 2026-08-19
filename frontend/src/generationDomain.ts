import type { BatchStatus, GenerationBatch, GenerationSummary, PromptRevisionSummary } from './api'

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
