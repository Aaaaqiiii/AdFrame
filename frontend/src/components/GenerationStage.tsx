import { useState } from 'react'
import type { GenerationBatch, GenerationSummary } from '../api'
import { generationBatchMergedDownloadUrl, generationContentUrl, generationDownloadUrl, prepareGenerationBatchMergedDownload } from '../api'
import { batchStatusLabel, canResolveUncertain, canRetryGeneration, generationPhaseLabel, latestGenerationPerPosition } from '../generationDomain'

type Props = {
  batch: GenerationBatch | null
  retryingGenerationId: string | null
  onRetry: (generationId: string) => Promise<void>
  onResolve: (generationId: string, resolution: 'submitted' | 'not_submitted', externalTaskId?: string) => Promise<void>
  onRefresh: () => Promise<void>
  onBackToPrompt: () => void
}

function ResultCard(p: {
  generation: GenerationSummary
  projectId: string
  retrying: boolean
  onRetry: () => void
  onResolve: (resolution: 'submitted' | 'not_submitted', externalTaskId?: string) => void
}) {
  const { generation, projectId } = p
  const status = generation.status
  const [resolveTaskId, setResolveTaskId] = useState('')
  return <div className="generation-result-card">
    <div className="generation-result-head">
      <strong>片段 {generation.batch_position ?? '?'}</strong>
      <span className={`status-badge ${status === 'completed' ? 'success' : status === 'failed' ? 'danger' : status === 'submission_uncertain' ? 'warn' : ''}`}>{generationPhaseLabel(generation)}</span>
    </div>
    {generation.error_message && <div className="generation-result-error">{generation.error_message}</div>}
    {status === 'completed' && generation.local_video_url && (
      <div className="generation-result-video">
        <video controls src={generationContentUrl(projectId, generation.id)} preload="metadata" />
        <a className="button secondary" href={generationDownloadUrl(projectId, generation.id)} download>下载本段</a>
      </div>
    )}
    {canRetryGeneration(generation) && (
      <button type="button" className="button secondary" disabled={p.retrying} onClick={p.onRetry}>{p.retrying ? '重试中…' : '重试本段'}</button>
    )}
    {canResolveUncertain(generation) && (
      <div className="generation-result-resolve">
        <span>提交状态不确定，请确认供应商是否创建了任务：</span>
        <label>供应商任务 ID <input value={resolveTaskId} onChange={(event) => setResolveTaskId(event.target.value)} placeholder="粘贴供应商返回的任务 ID" /></label>
        <button type="button" className="button secondary" disabled={!resolveTaskId.trim()} onClick={() => p.onResolve('submitted', resolveTaskId.trim())}>已创建任务</button>
        <button type="button" className="button secondary" onClick={() => p.onResolve('not_submitted')}>未创建任务</button>
      </div>
    )}
  </div>
}

export function GenerationStage(p: Props) {
  const batch = p.batch
  const [mergeBusy, setMergeBusy] = useState(false)
  const [mergeError, setMergeError] = useState('')

  async function downloadMergedVideo() {
    if (!batch || mergeBusy) return
    setMergeBusy(true)
    setMergeError('')
    try {
      await prepareGenerationBatchMergedDownload(batch.project_id, batch.generation_batch_id)
      const link = document.createElement('a')
      link.href = generationBatchMergedDownloadUrl(batch.project_id, batch.generation_batch_id)
      link.download = `complete-video-v${batch.prompt_version}.mp4`
      document.body.appendChild(link)
      link.click()
      link.remove()
    } catch (error) {
      setMergeError(error instanceof Error ? error.message : '完整视频下载失败')
    } finally {
      setMergeBusy(false)
    }
  }

  return <section className="stage-content generation-stage">
    <div className="stage-heading">
      <div>
        <span className="eyebrow">第七步</span>
        <h1>生成与结果</h1>
        <p>每段可以独立播放与下载；全部完成后可无损合并为一个完整视频。</p>
      </div>
      <div className="generation-stage-actions">
        {batch && <span className="version-badge">{batchStatusLabel(batch.status)}</span>}
        <button type="button" className="button secondary" onClick={p.onRefresh}>刷新</button>
        <button type="button" className="button secondary" onClick={p.onBackToPrompt}>返回提示词</button>
      </div>
    </div>
    {!batch && <div className="generation-placeholder">尚未提交生成批次。请先返回提示词步骤，确认完整提示词后点击“开始生成全部片段”。</div>}
    {batch && <>
      <div className="generation-batch-summary">
        <strong>供应商：{batch.provider === 'volcengine' ? '火山 Seedance 2.5' : 'Comfly Seedance 2.5'}</strong>
        <span>提示词版本 v{batch.prompt_version}</span>
        <span>共 {batch.batch_size} 段</span>
        <span>状态：{batchStatusLabel(batch.status)}</span>
        {batch.status === 'complete' && <button type="button" className="button primary" disabled={mergeBusy} onClick={() => void downloadMergedVideo()}>{mergeBusy ? '正在无损合并…' : '无损合并并下载完整视频'}</button>}
      </div>
      {mergeError && <div className="generation-result-error generation-merge-error">{mergeError}</div>}
      <div className="generation-results">
        {latestGenerationPerPosition(batch).map((generation) => (
          <ResultCard
            key={generation.id}
            generation={generation}
            projectId={batch.project_id}
            retrying={p.retryingGenerationId === generation.id}
            onRetry={() => void p.onRetry(generation.id)}
            onResolve={(resolution, externalTaskId) => void p.onResolve(generation.id, resolution, externalTaskId)}
          />
        ))}
      </div>
    </>}
  </section>
}
