export type ProjectMode = 'preserve_product' | 'replace_product'
export type WorkflowStage = 'materials' | 'analysis' | 'timeline' | 'shots' | 'prompt'

export function modeFromPath(pathname: string): ProjectMode {
  return pathname.startsWith('/replace-product') ? 'replace_product' : 'preserve_product'
}

export function modePath(mode: ProjectMode) {
  return mode === 'replace_product' ? '/replace-product' : '/preserve-product'
}

export function canBeginVideoAnalysis(mode: ProjectMode, hasVideo: boolean, hasProduct: boolean) {
  if (!hasVideo) return { ready: false, reason: '请先上传参考视频。' }
  if (mode === 'replace_product' && !hasProduct) {
    return { ready: false, reason: '请先上传目标产品图，AI 将先生成可编辑的产品档案。' }
  }
  return { ready: true, reason: '' }
}

export function nextWorkflowStage(input: {
  hasVideo: boolean
  hasRequiredProduct: boolean
  hasShots: boolean
  timelineSaved: boolean
  shotsReady: boolean
}): WorkflowStage {
  if (!input.hasVideo || !input.hasRequiredProduct) return 'materials'
  if (!input.hasShots) return 'analysis'
  if (!input.timelineSaved) return 'timeline'
  if (input.shotsReady) return 'shots'
  return 'analysis'
}

export function shouldRefreshProjectDuringPolling(input: {
  globalStatus?: string | null
  timelineSource?: string | null
  timelineDirty: boolean
  hasActiveShotJobs: boolean
  stage: WorkflowStage
}) {
  if (input.timelineDirty || input.stage === 'timeline') return false
  const initialAnalysisFinished = ['succeeded', 'completed'].includes(input.globalStatus || '')
    && input.timelineSource === 'ffmpeg_candidates'
  return initialAnalysisFinished || input.hasActiveShotJobs
}

export function analysisStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    pending: '未开始',
    not_started: '未开始',
    queued: '排队中',
    uploaded: '排队中',
    running: '分析中',
    processing: '分析中',
    retryable: '等待重试',
    succeeded: '分析成功',
    completed: '分析成功',
    failed: '分析失败',
  }
  return labels[status || ''] || '未开始'
}

// 镜头自身的成功状态代表已有可用事实，不能被历史任务状态降级覆盖。
export function effectiveShotAnalysisStatus(shotStatus?: string | null, latestJobStatus?: string | null) {
  if (shotStatus === 'succeeded' || shotStatus === 'completed') return shotStatus
  return latestJobStatus || shotStatus || 'pending'
}

// 后台刷新项目时保留用户正在校对的镜头；只有原镜头不存在时才回到第一个。
export function preserveShotSelection(currentId: string, shots: Array<{ id: string }>) {
  return shots.some((shot) => shot.id === currentId) ? currentId : shots[0]?.id || ''
}

export function statusTone(status?: string | null) {
  if (status === 'failed') return 'danger'
  if (status === 'succeeded' || status === 'completed') return 'success'
  if (['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(status || '')) return 'working'
  return 'neutral'
}
