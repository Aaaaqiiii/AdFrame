import type { MaterialState } from './MaterialsStage'
import { AssetCard } from './AssetCard'
import type { AnalysisJob, GenerationSegmentPlan, PromptRevisionSummary } from '../api'
import { batchTaskCountLabel, canCreateBatch } from '../batchSubmit'

type Props = {
  mode: 'preserve_product' | 'replace_product'
  promptMode: 'full_reference_video_edit' | 'standalone_video_recreation'
  onPromptMode: (value: 'full_reference_video_edit' | 'standalone_video_recreation') => void
  recreationAudioMode: 'none' | 'auto' | 'custom'
  recreationAudioRequirement: string
  prompt: string
  promptDirty: boolean
  direction: string
  refinement: string
  promptVersion: number
  personReady: boolean
  replacePerson: boolean
  backgroundReady: boolean
  busy: 'generate' | 'refine' | 'optimize' | null
  versions: PromptRevisionSummary[]
  selectedVersion: number
  currentTimelineRevisionId?: string
  taskStatus?: AnalysisJob
  blockingReason?: string
  onPrompt: (value: string) => void
  onDirection: (value: string) => void
  onRecreationAudioMode: (value: 'none' | 'auto' | 'custom') => void
  onRecreationAudioRequirement: (value: string) => void
  onCopyPrompt: () => void
  onRefinement: (value: string) => void
  onReplacePerson: (value: boolean) => void
  onSelectVersion: (version: number) => void
  onGenerate: () => void
  onRefine: () => void
  onSave: () => void
  onCancelPromptTask: () => void
  cancellingPromptTask: boolean
  person: MaterialState
  onPerson: (file: File) => void
  onPersonRetry: () => void
  onPersonProfile: (value: string) => void
  onPersonProfileSave: () => void
  personDeleting: boolean
  onPersonDelete: () => void
  // 批次生成控制。
  generationPlan: GenerationSegmentPlan | null
  selectedPromptRevision: PromptRevisionSummary | null
  selectedProvider: 'volcengine' | 'comfly'
  generationRatio: 'adaptive' | '16:9' | '4:3' | '1:1' | '3:4' | '9:16' | '21:9'
  optimizeBusy: boolean
  generationBusy: boolean
  segmentBusy: boolean
  onOptimizeSellingPoints: () => void
  onProviderChange: (provider: 'volcengine' | 'comfly') => void
  onGenerationRatioChange: (ratio: 'adaptive' | '16:9' | '4:3' | '1:1' | '3:4' | '9:16' | '21:9') => void
  onAutoPlanSegments: () => void
  onCreateBatch: () => void
}

export function PromptStage(p: Props) {
  const visibleVersions = p.versions.filter((version) => version.prompt_mode === p.promptMode)
  const audioRequirementMissing = p.recreationAudioMode === 'custom' && !p.recreationAudioRequirement.trim()
  const cannotGenerate = Boolean(p.blockingReason) || (p.replacePerson && !p.personReady) || audioRequirementMissing
  const activeTask = p.taskStatus && ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(p.taskStatus.status)
  const batchBlocking = p.promptDirty ? '当前提示词或生成设置尚未保存，请先保存为新版本' : canCreateBatch(p.generationPlan, p.selectedPromptRevision, p.generationBusy)
  const taskCountLabel = batchTaskCountLabel(p.generationPlan)
  return <section className="stage-content prompt-stage">
    <div className="stage-heading"><div><span className="eyebrow">第五步</span><h1>生成并校对完整提示词</h1><p>GPT只读取人工确认的分镜事实、参考档案和你的替换选择。</p></div><span className="version-badge">{p.promptVersion ? `已保存 v${p.promptVersion}` : '尚未保存'}</span></div>
    <div className="prompt-mode-switch" role="group" aria-label="提示词类型">
      <button type="button" className={p.promptMode === 'full_reference_video_edit' ? 'active' : ''} disabled={Boolean(p.busy)} onClick={() => p.onPromptMode('full_reference_video_edit')}><strong>参考视频改编指令</strong><small>保留现有的保持、修改、删除、禁止格式，生成时使用参考视频。</small></button>
      <button type="button" className={p.promptMode === 'standalone_video_recreation' ? 'active' : ''} disabled={Boolean(p.busy)} onClick={() => p.onPromptMode('standalone_video_recreation')}><strong>独立视频复刻提示词</strong><small>把每个镜头完整写出来，提示词脱离参考视频也能单独使用。</small></button>
    </div>
    {p.blockingReason && <div className="compatibility-banner blocked"><strong>当前还不能生成提示词</strong><span>{p.blockingReason}</span></div>}
    {p.mode === 'replace_product' && <div className="compatibility-banner"><strong>目标产品已锁定</strong><span>使用第一步确认的目标产品档案，所有出现原产品的镜头都会替换为该产品；包装结构、颜色、Logo 和文字会写入最终提示词。</span></div>}
    <div className="material-grid prompt-target-product">
      <AssetCard title="人物参考图或文字描述" description="以参考图为主；文字只需填写年龄段、发型、主服装和显著特征，一两句话即可。" filename={p.person.filename} previewUrl={p.person.previewUrl} images={p.person.images} allowManualProfile profileHint="人物原图会随每个生成片段提交" deleting={p.personDeleting} accept="image/jpeg,image/png,image/webp" status={p.person.status} error={p.person.error} profile={p.person.profile} onFile={p.onPerson} onRetry={p.onPersonRetry} onProfile={p.onPersonProfile} onSaveProfile={p.onPersonProfileSave} onDelete={p.onPersonDelete} />
    </div>
    <div className="prompt-options">
      <label className="choice-card"><input type="checkbox" checked={p.replacePerson} onChange={(event) => p.onReplacePerson(event.target.checked)} /><span><strong>替换人物</strong><small>{p.personReady ? p.person.filename ? '使用已确认的人物图片档案' : '使用已确认的人物文字档案' : '请先填写人物文字档案，或上传并确认人物图'}</small></span></label>
    </div>
    <div className="recreation-audio-controls">
      <label><strong>声音处理</strong><select value={p.recreationAudioMode} onChange={(event) => p.onRecreationAudioMode(event.target.value as Props['recreationAudioMode'])}><option value="none">无音频</option><option value="auto">自动生成音频</option><option value="custom">用户填写音频要求</option></select></label>
      <small>{p.recreationAudioMode === 'none' ? '每个镜头明确写为不生成音频。' : p.recreationAudioMode === 'auto' ? '自动设计环境音、动作音和背景音乐，不新增对白、旁白或营销口播。' : '逐镜落实你的音频要求；未明确要求时不新增对白、旁白或营销口播。'}</small>
      {p.recreationAudioMode === 'custom' && <textarea value={p.recreationAudioRequirement} onChange={(event) => p.onRecreationAudioRequirement(event.target.value)} placeholder="例如：轻快电子音乐，保留开盖声和产品落桌声，不要旁白。" />}
      {audioRequirementMissing && <span className="field-error">请填写音频要求。</span>}
    </div>
    <label className="prompt-direction"><strong>{p.promptMode === 'standalone_video_recreation' ? '复刻要求' : '改编要求'}</strong><textarea value={p.direction} onChange={(event) => p.onDirection(event.target.value)} placeholder={p.promptMode === 'standalone_video_recreation' ? '例如：完整描述每个镜头；目标产品直接写成最终画面事实；保持镜头时长和顺序。' : '例如：背景改为海边，保持原动作节奏、运镜和BGM。'} /></label>
    <div className="prompt-layout"><main><header><div><strong>{p.promptMode === 'standalone_video_recreation' ? '独立复刻提示词' : '最终提示词'}</strong><span>生成后可直接修改，保存时不会再次被AI改写</span></div><div className="prompt-version-row"><label>版本<select className="prompt-version-select" value={p.selectedVersion || ''} disabled={!visibleVersions.length || Boolean(p.busy)} onChange={(event) => p.onSelectVersion(Number(event.target.value))}>{!visibleVersions.length && <option value="">当前类型尚无已完成版本</option>}{visibleVersions.map((version) => <option key={version.id} value={version.version}>{`v${version.version} ${new Date(version.created_at).toLocaleString()}${version.source_timeline_revision_id !== p.currentTimelineRevisionId ? '（旧时间轴）' : ''}`}</option>)}</select></label>{activeTask && <><span className="prompt-task-state">GPT 提示词任务处理中</span><button type="button" className="button danger compact" disabled={p.cancellingPromptTask} onClick={p.onCancelPromptTask}>{p.cancellingPromptTask ? '取消中…' : '取消当前任务'}</button></>}{p.taskStatus?.status === 'failed' && <span className="prompt-task-state failed">上次 GPT 任务失败：{p.taskStatus.error_message || '未知错误'}</span>}</div></header><textarea className="prompt-textarea" value={p.prompt} onChange={(event) => p.onPrompt(event.target.value)} placeholder={p.promptMode === 'standalone_video_recreation' ? '点击“由 GPT 生成独立复刻提示词”' : '点击“由 GPT 生成提示词”'} /></main></div>
    <label className="prompt-direction prompt-refinement"><strong>让 GPT-5.6 继续修改</strong><small>当前版本会保留，修改结果保存为新的提示词版本。</small><textarea value={p.refinement} onChange={(event) => p.onRefinement(event.target.value)} placeholder="例如：每个镜头都补充产品包装约束；保持时间轴不变；把整体风格改得更高级。" /><button type="button" className="button secondary" disabled={!p.prompt.trim() || !p.refinement.trim() || Boolean(p.busy)} onClick={p.onRefine}>{p.busy === 'refine' ? 'GPT 正在修改…' : '按要求修改提示词'}</button></label>
    <footer className="prompt-actions"><button className="button secondary" disabled={!p.prompt.trim() || Boolean(p.busy)} onClick={p.onSave}>保存人工版本</button>{p.promptMode === 'standalone_video_recreation' && <button className="button secondary" disabled={!p.prompt.trim()} onClick={p.onCopyPrompt}>复制提示词</button>}<button className="button secondary" disabled={p.promptMode !== 'full_reference_video_edit' || !p.prompt.trim() || Boolean(p.busy) || p.optimizeBusy} onClick={p.onOptimizeSellingPoints}>{p.optimizeBusy ? '卖点优化中…' : '根据产品卖点优化'}</button><button className="button primary large" disabled={cannotGenerate || Boolean(p.busy)} onClick={p.onGenerate}>{p.busy === 'generate' ? 'GPT 正在整理…' : p.promptMode === 'standalone_video_recreation' ? '由 GPT 生成独立复刻提示词' : '由 GPT 生成提示词'}</button></footer>
    {p.promptMode === 'full_reference_video_edit' ? <div className="generation-controls">
      <div className="generation-controls-heading"><strong>生成全部片段</strong><span>{taskCountLabel}</span></div>
      <div className="generation-controls-row">
        <label>供应商
          <select value={p.selectedProvider} onChange={(event) => p.onProviderChange(event.target.value as 'volcengine' | 'comfly')}>
            <option value="volcengine">火山 Seedance 2.5</option>
            <option value="comfly">Comfly Seedance 2.5</option>
          </select>
        </label>
        <label>视频比例
          <select value={p.generationRatio} onChange={(event) => p.onGenerationRatioChange(event.target.value as Props['generationRatio'])}>
            <option value="adaptive">跟随参考素材</option>
            <option value="9:16">9:16 竖屏</option>
            <option value="16:9">16:9 横屏</option>
            <option value="1:1">1:1 方形</option>
            <option value="4:3">4:3 横屏</option>
            <option value="3:4">3:4 竖屏</option>
            <option value="21:9">21:9 宽银幕</option>
          </select>
        </label>
        {(p.person.filename || p.backgroundReady) && <div className="generation-reference-summary"><strong>参考图自动提交</strong><small>{p.person.filename ? '人物参考原图将随每个片段提交。' : ''}{p.backgroundReady ? '背景参考原图将随每个片段提交。' : ''}</small></div>}
      </div>
      {batchBlocking && <div className="compatibility-banner blocked"><strong>当前还不能生成</strong><span>{batchBlocking}</span>{(!p.generationPlan || p.generationPlan.plan_version <= 0 || !p.generationPlan.segments.length) && <button type="button" className="button secondary" disabled={p.segmentBusy} onClick={p.onAutoPlanSegments}>{p.segmentBusy ? '规划中…' : '自动规划并保存'}</button>}</div>}
      <button type="button" className="button primary large" disabled={Boolean(batchBlocking)} onClick={p.onCreateBatch}>开始生成全部片段</button>
    </div> : <div className="compatibility-banner"><strong>独立复刻提示词已与视频生成解耦</strong><span>可复制到文生视频服务直接使用；系统内 Seedance 批量生成仍使用“参考视频改编指令”。</span></div>}
  </section>
}
