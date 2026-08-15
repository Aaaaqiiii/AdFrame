import type { MaterialState } from './MaterialsStage'
import { AssetCard } from './AssetCard'

type Props = {
  mode: 'preserve_product' | 'replace_product'
  prompt: string
  direction: string
  refinement: string
  promptVersion: number
  productReady: boolean
  personReady: boolean
  replaceProduct: boolean
  replacePerson: boolean
  busy: 'generate' | 'refine' | null
  blockingReason?: string
  onPrompt: (value: string) => void
  onDirection: (value: string) => void
  onRefinement: (value: string) => void
  onReplaceProduct: (value: boolean) => void
  onReplacePerson: (value: boolean) => void
  onGenerate: () => void
  onRefine: () => void
  onSave: () => void
  targetProduct: MaterialState
  onTargetProduct: (file: File) => void
  onTargetProducts: (files: File[]) => void
  onTargetProductRetry: () => void
  onTargetProductProfile: (value: string) => void
  onTargetProductProfileSave: () => void
  person: MaterialState
  onPerson: (file: File) => void
  onPersonRetry: () => void
  onPersonProfile: (value: string) => void
  onPersonProfileSave: () => void
}

export function PromptStage(p: Props) {
  const cannotGenerate = Boolean(p.blockingReason) || (p.replaceProduct && !p.productReady) || (p.replacePerson && !p.personReady)
  return <section className="stage-content prompt-stage">
    <div className="stage-heading"><div><span className="eyebrow">第五步</span><h1>生成并校对完整提示词</h1><p>GPT只读取人工确认的分镜事实、参考档案和你的替换选择。</p></div><span className="version-badge">{p.promptVersion ? `已保存 v${p.promptVersion}` : '尚未保存'}</span></div>
    {p.blockingReason && <div className="compatibility-banner blocked"><strong>当前还不能生成提示词</strong><span>{p.blockingReason}</span></div>}
    {p.mode === 'replace_product' && <div className="compatibility-banner"><strong>目标产品已锁定</strong><span>使用第一步确认的目标产品档案，所有出现原产品的镜头都会替换为该产品；包装结构、颜色、Logo 和文字会写入最终提示词。</span></div>}
    <div className="material-grid prompt-target-product">
      {p.mode === 'preserve_product' && <AssetCard title="目标产品图" description="支持一次选择或继续追加多张；AI 会综合正面、侧面、背面和细节图生成一份产品档案。" filename={p.targetProduct.filename} previewUrl={p.targetProduct.previewUrl} images={p.targetProduct.images} multiple accept="image/jpeg,image/png,image/webp" status={p.targetProduct.status} error={p.targetProduct.error} profile={p.targetProduct.profile} onFile={p.onTargetProduct} onFiles={p.onTargetProducts} onRetry={p.onTargetProductRetry} onProfile={p.onTargetProductProfile} onSaveProfile={p.onTargetProductProfileSave} />}
      <AssetCard title="人物参考图或文字描述" description="有照片可以上传；没有照片时直接填写人物描述，保存后同样可以用于替换人物。" filename={p.person.filename} previewUrl={p.person.previewUrl} images={p.person.images} allowManualProfile accept="image/jpeg,image/png,image/webp" status={p.person.status} error={p.person.error} profile={p.person.profile} onFile={p.onPerson} onRetry={p.onPersonRetry} onProfile={p.onPersonProfile} onSaveProfile={p.onPersonProfileSave} />
    </div>
    <div className="prompt-options">
      {p.mode === 'preserve_product' && <label className="choice-card"><input type="checkbox" checked={p.replaceProduct} onChange={(event) => p.onReplaceProduct(event.target.checked)} /><span><strong>替换产品</strong><small>{p.productReady ? '使用已确认的产品图片档案' : '请先上传并确认产品图档案'}</small></span></label>}
      <label className="choice-card"><input type="checkbox" checked={p.replacePerson} onChange={(event) => p.onReplacePerson(event.target.checked)} /><span><strong>替换人物</strong><small>{p.personReady ? '使用已确认的人物图片档案' : '请先上传并确认人物图档案'}</small></span></label>
    </div>
    <label className="prompt-direction"><strong>改编要求</strong><textarea value={p.direction} onChange={(event) => p.onDirection(event.target.value)} placeholder="例如：背景改为海边，保持原动作节奏、运镜和BGM。" /></label>
    <div className="prompt-layout"><main><header><strong>最终提示词</strong><span>生成后可直接修改，保存时不会再次被AI改写</span></header><textarea className="prompt-textarea" value={p.prompt} onChange={(event) => p.onPrompt(event.target.value)} placeholder="点击“由 GPT 生成提示词”" /></main></div>
    <label className="prompt-direction prompt-refinement"><strong>让 GPT-5.6 继续修改</strong><small>当前版本会保留，修改结果保存为新的提示词版本。</small><textarea value={p.refinement} onChange={(event) => p.onRefinement(event.target.value)} placeholder="例如：每个镜头都补充产品包装约束；保持时间轴不变；把整体风格改得更高级。" /><button type="button" className="button secondary" disabled={!p.prompt.trim() || !p.refinement.trim() || Boolean(p.busy)} onClick={p.onRefine}>{p.busy === 'refine' ? 'GPT 正在修改…' : '按要求修改提示词'}</button></label>
    <footer className="prompt-actions"><button className="button secondary" disabled={!p.prompt.trim() || Boolean(p.busy)} onClick={p.onSave}>保存人工版本</button><button className="button primary large" disabled={cannotGenerate || Boolean(p.busy)} onClick={p.onGenerate}>{p.busy === 'generate' ? 'GPT 正在整理…' : '由 GPT 生成提示词'}</button></footer>
  </section>
}
