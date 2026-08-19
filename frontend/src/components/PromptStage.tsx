import type { MaterialState } from './MaterialsStage'
import { AssetCard } from './AssetCard'
import type { AnalysisJob, GenerationSegmentPlan, PromptRevisionSummary } from '../api'
import { batchTaskCountLabel, canCreateBatch } from '../batchSubmit'

type Props = {
  mode: 'preserve_product' | 'replace_product'
  prompt: string
  direction: string
  refinement: string
  promptVersion: number
  personReady: boolean
  replacePerson: boolean
  busy: 'generate' | 'refine' | 'optimize' | null
  versions: PromptRevisionSummary[]
  selectedVersion: number
  taskStatus?: AnalysisJob
  blockingReason?: string
  onPrompt: (value: string) => void
  onDirection: (value: string) => void
  onRefinement: (value: string) => void
  onReplacePerson: (value: boolean) => void
  onSelectVersion: (version: number) => void
  onGenerate: () => void
  onRefine: () => void
  onSave: () => void
  person: MaterialState
  onPerson: (file: File) => void
  onPersonRetry: () => void
  onPersonProfile: (value: string) => void
  onPersonProfileSave: () => void
  // 批次生成控制。
  generationPlan: GenerationSegmentPlan | null
  selectedPromptRevision: PromptRevisionSummary | null
  selectedProvider: 'volcengine' | 'comfly'
  generateAudio: boolean
  includePersonReference: boolean
  includeBackgroundReference: boolean
  optimizeBusy: boolean
  generationBusy: boolean
  onOptimizeSellingPoints: () => void
  onProviderChange: (provider: 'volcengine' | 'comfly') => void
  onGenerateAudioChange: (enabled: boolean) => void
  onIncludePersonReferenceChange: (enabled: boolean) => void
  onIncludeBackgroundReferenceChange: (enabled: boolean) => void
  onCreateBatch: () => void
}

export function PromptStage(p: Props) {
  const cannotGenerate = Boolean(p.blockingReason) || (p.replacePerson && !p.personReady)
  const activeTask = p.taskStatus && ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(p.taskStatus.status)
  const batchBlocking = canCreateBatch(p.generationPlan, p.selectedPromptRevision, p.generationBusy)
  const taskCountLabel = batchTaskCountLabel(p.generationPlan)
  return <section className="stage-content prompt-stage">
    <div className="stage-heading"><div><span className="eyebrow">第五步</span><h1>生成并校对完整提示词</h1><p>GPT只读取人工确认的分镜事实、参考档案和你的替换选择。</p></div><span className="version-badge">{p.promptVersion ? `已保存 v${p.promptVersion}` : '尚未保存'}</span></div>
    {p.blockingReason && <div className="compatibility-banner blocked"><strong>当前还不能生成提示词</strong><span>{p.blockingReason}</span></div>}
    {p.mode === 'replace_product' && <div className="compatibility-banner"><strong>目标产品已锁定</strong><span>使用第一步确认的目标产品档案，所有出现原产品的镜头都会替换为该产品；包装结构、颜色、Logo 和文字会写入最终提示词。</span></div>}
    <div className="material-grid prompt-target-product">
      <AssetCard title="人物参考图或文字描述" description="有照片可以上传；没有照片时直接填写人物描述，保存后同样可以用于替换人物。" filename={p.person.filename} previewUrl={p.person.previewUrl} images={p.person.images} allowManualProfile accept="image/jpeg,image/png,image/webp" status={p.person.status} error={p.person.error} profile={p.person.profile} onFile={p.onPerson} onRetry={p.onPersonRetry} onProfile={p.onPersonProfile} onSaveProfile={p.onPersonProfileSave} />
    </div>
    <div className="prompt-options">
      <label className="choice-card"><input type="checkbox" checked={p.replacePerson} onChange={(event) => p.onReplacePerson(event.target.checked)} /><span><strong>替换人物</strong><small>{p.personReady ? '使用已确认的人物图片档案' : '请先上传并确认人物图档案'}</small></span></label>
    </div>
    <label className="prompt-direction"><strong>改编要求</strong><textarea value={p.direction} onChange={(event) => p.onDirection(event.target.value)} placeholder="例如：背景改为海边，保持原动作节奏、运镜和BGM。" /></label>
    <div className="prompt-layout"><main><header><div><strong>最终提示词</strong><span>生成后可直接修改，保存时不会再次被AI改写</span></div><div className="prompt-version-row"><label>版本<select className="prompt-version-select" value={p.selectedVersion || ''} disabled={!p.versions.length || Boolean(p.busy)} onChange={(event) => p.onSelectVersion(Number(event.target.value))}>{!p.versions.length && <option value="">尚无已完成版本</option>}{p.versions.map((version) => <option key={version.id} value={version.version}>{`v${version.version} ${new Date(version.created_at).toLocaleString()}`}</option>)}</select></label>{activeTask && <span className="prompt-task-state">GPT 提示词任务处理中</span>}{p.taskStatus?.status === 'failed' && <span className="prompt-task-state failed">上次 GPT 任务失败：{p.taskStatus.error_message || '未知错误'}</span>}</div></header><textarea className="prompt-textarea" value={p.prompt} onChange={(event) => p.onPrompt(event.target.value)} placeholder="点击“由 GPT 生成提示词”" /></main></div>
    <label className="prompt-direction prompt-refinement"><strong>让 GPT-5.6 继续修改</strong><small>当前版本会保留，修改结果保存为新的提示词版本。</small><textarea value={p.refinement} onChange={(event) => p.onRefinement(event.target.value)} placeholder="例如：每个镜头都补充产品包装约束；保持时间轴不变；把整体风格改得更高级。" /><button type="button" className="button secondary" disabled={!p.prompt.trim() || !p.refinement.trim() || Boolean(p.busy)} onClick={p.onRefine}>{p.busy === 'refine' ? 'GPT 正在修改…' : '按要求修改提示词'}</button></label>
    <footer className="prompt-actions"><button className="button secondary" disabled={!p.prompt.trim() || Boolean(p.busy)} onClick={p.onSave}>保存人工版本</button><button className="button secondary" disabled={!p.prompt.trim() || Boolean(p.busy) || p.optimizeBusy} onClick={p.onOptimizeSellingPoints}>{p.optimizeBusy ? '卖点优化中…' : '根据产品卖点优化'}</button><button className="button primary large" disabled={cannotGenerate || Boolean(p.busy)} onClick={p.onGenerate}>{p.busy === 'generate' ? 'GPT 正在整理…' : '由 GPT 生成提示词'}</button></footer>
    <div className="generation-controls">
      <div className="generation-controls-heading"><strong>生成全部片段</strong><span>{taskCountLabel}</span></div>
      <div className="generation-controls-row">
        <label>供应商
          <select value={p.selectedProvider} onChange={(event) => p.onProviderChange(event.target.value as 'volcengine' | 'comfly')}>
            <option value="volcengine">火山 Seedance 2.5</option>
            <option value="comfly">Comfly Seedance 2.5</option>
          </select>
        </label>
        <label className="choice-card"><input type="checkbox" checked={p.generateAudio} onChange={(event) => p.onGenerateAudioChange(event.target.checked)} /><span><strong>生成音频</strong><small>要求 Seedance 输出音频</small></span></label>
        <label className="choice-card"><input type="checkbox" checked={p.includePersonReference} onChange={(event) => p.onIncludePersonReferenceChange(event.target.checked)} /><span><strong>包含人物参考图</strong></span></label>
        <label className="choice-card"><input type="checkbox" checked={p.includeBackgroundReference} onChange={(event) => p.onIncludeBackgroundReferenceChange(event.target.checked)} /><span><strong>包含背景参考图</strong></span></label>
      </div>
      {batchBlocking && <div className="compatibility-banner blocked"><strong>当前还不能生成</strong><span>{batchBlocking}</span></div>}
      <button type="button" className="button primary large" disabled={Boolean(batchBlocking)} onClick={p.onCreateBatch}>开始生成全部片段</button>
    </div>
  </section>
}
