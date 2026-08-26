import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { PromptStage } from './PromptStage'

const noop = vi.fn()

function render(promptMode: 'full_reference_video_edit' | 'standalone_video_recreation', promptDirty = false, personLoaded: 'none' | 'text' | 'image' = 'none', taskStatus?: Parameters<typeof PromptStage>[0]['taskStatus']) {
  return renderToStaticMarkup(<PromptStage
    mode="replace_product"
    promptMode={promptMode}
    onPromptMode={noop}
    recreationAudioMode="custom"
    recreationAudioRequirement="保留开盖声，不要旁白"
    prompt="完整提示词"
    promptDirty={promptDirty}
    direction="完整复刻"
    refinement=""
    promptVersion={1}
    personReady
    replacePerson={false}
    backgroundReady={false}
    busy={null}
    versions={[]}
    selectedVersion={1}
    currentTimelineRevisionId="timeline-1"
    taskStatus={taskStatus}
    onPrompt={noop}
    onDirection={noop}
    onRecreationAudioMode={noop}
    onRecreationAudioRequirement={noop}
    onCopyPrompt={noop}
    onRefinement={noop}
    onReplacePerson={noop}
    onSelectVersion={noop}
    onGenerate={noop}
    onRefine={noop}
    onSave={noop}
    person={personLoaded === 'image'
      ? { filename: 'person.png', previewUrl: '/person.png', images: [], status: 'succeeded', profile: '年轻女性，黑色长发', error: '' }
      : personLoaded === 'text'
        ? { filename: '', previewUrl: '', images: [], status: 'succeeded', profile: '年轻女性，黑色长发', error: '' }
        : { filename: '', previewUrl: '', images: [], status: 'pending', profile: '', error: '' }}
    onPerson={noop}
    onPersonRetry={noop}
    onPersonProfile={noop}
    onPersonProfileSave={noop}
    personDeleting={false}
    onPersonDelete={noop}
    generationPlan={null}
    selectedPromptRevision={null}
    selectedProvider="volcengine"
    generationRatio="adaptive"
    optimizeBusy={false}
    generationBusy={false}
    segmentBusy={false}
    onOptimizeSellingPoints={noop}
    onProviderChange={noop}
    onGenerationRatioChange={noop}
    onAutoPlanSegments={noop}
    onCreateBatch={noop}
    onCancelPromptTask={noop}
    cancellingPromptTask={false}
  />)
}

describe('PromptStage prompt modes', () => {
  it('shows recreation audio and copy controls without Seedance batch controls', () => {
    const html = render('standalone_video_recreation')
    expect(html).toContain('声音处理')
    expect(html).toContain('复制提示词')
    expect(html).toContain('保留开盖声，不要旁白')
    expect(html).not.toContain('火山 Seedance 2.5')
  })

  it('keeps Seedance controls on the reference-edit mode', () => {
    const html = render('full_reference_video_edit')
    expect(html).toContain('火山 Seedance 2.5')
    expect(html).toContain('声音处理')
    expect(html).not.toContain('复制提示词')
  })

  it('blocks paid generation while the visible prompt has unsaved changes', () => {
    const html = render('full_reference_video_edit', true)
    expect(html).toContain('当前提示词或生成设置尚未保存')
    expect(html).toContain('开始生成全部片段')
  })

  it('shows delete control and accurate submission hint for a person image', () => {
    const html = render('full_reference_video_edit', false, 'image')
    expect(html).toContain('>删除</button>')
    expect(html).toContain('人物原图会随每个生成片段提交')
    expect(html).not.toContain('图片不会默认提交给 Seedance')
  })

  it('labels a text-only person profile without claiming an image exists', () => {
    const html = render('full_reference_video_edit', false, 'text')
    expect(html).toContain('使用已确认的人物文字档案')
    expect(html).not.toContain('使用已确认的人物图片档案')
  })

  it('shows a cancel control only while a prompt task is active', () => {
    const active = render('standalone_video_recreation', false, 'none', {
      job_id: 'prompt-job-1', kind: 'final_prompt_generation', status: 'processing', attempts: 1,
    })
    expect(active).toContain('取消当前任务')

    const completed = render('standalone_video_recreation', false, 'none', {
      job_id: 'prompt-job-1', kind: 'final_prompt_generation', status: 'completed', attempts: 1,
    })
    expect(completed).not.toContain('取消当前任务')
  })
})
