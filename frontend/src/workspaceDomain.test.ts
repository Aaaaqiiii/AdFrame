import { describe, expect, it } from 'vitest'

import {
  analysisStatusLabel,
  canBeginVideoAnalysis,
  effectiveShotAnalysisStatus,
  modeFromPath,
  modePath,
  nextWorkflowStage,
  preserveShotSelection,
  shouldRefreshProjectDuringPolling,
} from './workspaceDomain'

describe('two independent product workflows', () => {
  it('maps each direct URL to one explicit mode', () => {
    expect(modeFromPath('/preserve-product')).toBe('preserve_product')
    expect(modeFromPath('/replace-product')).toBe('replace_product')
    expect(modePath('replace_product')).toBe('/replace-product')
  })

  it('requires a target product image before page two can analyze', () => {
    expect(canBeginVideoAnalysis('replace_product', true, false)).toEqual({
      ready: false,
      reason: '请先上传目标产品图，AI 将先生成可编辑的产品档案。',
    })
    expect(canBeginVideoAnalysis('replace_product', true, true).ready).toBe(true)
    expect(canBeginVideoAnalysis('preserve_product', true, false).ready).toBe(true)
  })

  it('only opens shot editing after the manual timeline is confirmed', () => {
    expect(nextWorkflowStage({ hasVideo: true, hasRequiredProduct: true, hasShots: true, timelineSaved: false, shotsReady: true })).toBe('timeline')
    expect(nextWorkflowStage({ hasVideo: true, hasRequiredProduct: true, hasShots: true, timelineSaved: true, shotsReady: true })).toBe('shots')
  })

  it('never replaces a timeline while the user is correcting its boundaries', () => {
    expect(shouldRefreshProjectDuringPolling({
      globalStatus: 'succeeded', timelineSource: 'vision_hybrid', timelineDirty: true,
      hasActiveShotJobs: false, stage: 'timeline',
    })).toBe(false)
    expect(shouldRefreshProjectDuringPolling({
      globalStatus: 'succeeded', timelineSource: 'ffmpeg_candidates', timelineDirty: false,
      hasActiveShotJobs: false, stage: 'analysis',
    })).toBe(true)
  })
})

describe('analysis statuses', () => {
  it('presents every backend state in user language', () => {
    expect(analysisStatusLabel('queued')).toBe('排队中')
    expect(analysisStatusLabel('processing')).toBe('分析中')
    expect(analysisStatusLabel('retryable')).toBe('等待重试')
    expect(analysisStatusLabel('completed')).toBe('分析成功')
    expect(analysisStatusLabel('failed')).toBe('分析失败')
  })

  it('never lets an old job overwrite a successful shot state', () => {
    expect(effectiveShotAnalysisStatus('succeeded', 'queued')).toBe('succeeded')
    expect(effectiveShotAnalysisStatus('completed', 'failed')).toBe('completed')
    expect(effectiveShotAnalysisStatus('failed', 'queued')).toBe('queued')
  })

  it('keeps the current shot selected while project data refreshes', () => {
    const shots = [{ id: 'shot-1' }, { id: 'shot-2' }]
    expect(preserveShotSelection('shot-2', shots)).toBe('shot-2')
    expect(preserveShotSelection('removed-shot', shots)).toBe('shot-1')
  })
})
