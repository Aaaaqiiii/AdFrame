import type { AnalysisJob, ProductCompatibility, TimelineShot } from '../api'
import { effectiveShotAnalysisStatus } from '../workspaceDomain'
import { StatusBadge } from './StatusBadge'

export type EditDraft = {
  people: string
  action: string
  product: string
  productInteraction: string
  background: string
  camera: string
  lighting: string
  visualStyle: string
  visibleText: string
  keep: string
  uncertainties: string
  confirmed: boolean
}

type Props = {
  shots: TimelineShot[]
  selectedId: string
  jobs: AnalysisJob[]
  edit: EditDraft
  saving: boolean
  mode: 'preserve_product' | 'replace_product'
  compatibility: ProductCompatibility | null
  onSelect: (id: string) => void
  onEdit: (draft: EditDraft) => void
  onSave: (confirmed?: boolean) => void
  onRetry: (id: string) => void
  onAdoptAI: (id: string) => void
  onOpenPrompt: () => void
}

const fields: Array<[string, keyof EditDraft, string]> = [
  ['人物', 'people', '人数、外貌、发型、服装、表情和画面位置'],
  ['动作', 'action', '按时间顺序描述完整动作过程'],
  ['产品', 'product', '类别、外形、包装、颜色、材质、Logo、文字和位置'],
  ['产品交互', 'productInteraction', '人物如何拿取、接触、转动或使用产品'],
  ['背景', 'background', '场景、环境元素、前后景、景深和背景变化'],
  ['镜头', 'camera', '景别、角度、构图、运镜、对焦和稳定性'],
  ['光线', 'lighting', '方向、明暗、色调和对比度'],
  ['视觉风格', 'visualStyle', '广告画面的整体商业视觉风格'],
  ['画面文字', 'visibleText', '区分包装文字、后期字幕、广告标题和环境文字'],
  ['保持不变', 'keep', '后续改编默认保留的时长、动作节奏、展示角度、运镜和光线'],
  ['不确定项', 'uncertainties', '只保留真正影响后续修改、需要人工补充的问题'],
]

const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0')
  return `${minutes}:${(seconds % 60).toFixed(2).padStart(5, '0')}`
}

export function ShotWorkspace(p: Props) {
  const shot = p.shots.find((item) => item.id === p.selectedId) || p.shots[0]
  const job = p.jobs.find((item) => item.shot_id === shot?.id)
  const status = effectiveShotAnalysisStatus(shot?.analysis_status, job?.status || (shot?.action ? 'completed' : null))
  const confirmedCount = p.shots.filter((item) => item.edit?.confirmed).length

  return <section className="stage-content shot-stage">
    <div className="stage-heading"><div><span className="eyebrow">分镜事实</span><h1>检查GPT综合后的最终内容</h1><p>豆包与GPT的中间结果保留在后台。你只需修改最终事实并确认。</p></div><span className="version-badge">已确认 {confirmedCount}/{p.shots.length}</span></div>
    <div className="fact-card-workspace">
      <aside className="shot-browser"><header><strong>{p.shots.length} 个镜头</strong><small>最终事实状态</small></header>{p.shots.map((item, index) => {
        const itemJob = p.jobs.find((entry) => entry.shot_id === item.id)
        const itemStatus = item.edit?.confirmed ? 'completed' : effectiveShotAnalysisStatus(item.analysis_status, itemJob?.status)
        return <button key={item.id} className={item.id === shot?.id ? 'active' : ''} onClick={() => p.onSelect(item.id)}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>镜头 {index + 1}</strong><small>{formatTime(item.start_sec)}–{formatTime(item.end_sec)}</small></div><StatusBadge status={itemStatus} /></button>
      })}</aside>
      {shot && <main className="final-fact-card"><header><div><span className="eyebrow">镜头 {p.shots.indexOf(shot) + 1}</span><h2>{formatTime(shot.start_sec)}–{formatTime(shot.end_sec)}</h2></div><StatusBadge status={p.edit.confirmed ? 'completed' : status} /></header>
        {shot.has_new_ai_summary && <div className="compatibility-banner"><strong>本镜头有新的AI总结</strong><span>当前人工版本未被覆盖。你可以查看并采用新总结。</span><button type="button" className="button secondary" onClick={() => p.onAdoptAI(shot.id)}>查看并采用</button></div>}
        {status === 'failed' ? <div className="shot-failure"><strong>本镜头理解失败</strong><p>{job?.error_message || shot.analysis_error}</p><button className="button secondary" onClick={() => p.onRetry(shot.id)}>重新理解本镜头</button></div> : <form onSubmit={(event) => event.preventDefault()}>
          <div className="final-fact-fields">{fields.map(([label, key, hint]) => <label key={key}><span>{label}<small>{hint}</small></span><textarea value={String(p.edit[key])} onChange={(event) => p.onEdit({ ...p.edit, [key]: event.target.value, confirmed: false })} /></label>)}</div>
          <div className="fact-card-actions">
            <span className={`shot-save-feedback ${p.edit.confirmed ? 'visible' : ''}`} role="status" aria-live="polite">{p.edit.confirmed ? '✓ 本镜头已确认并保存' : ''}</span>
            <button type="button" className="button secondary" disabled={p.saving} onClick={() => p.onRetry(shot.id)}>重新理解本镜头</button>
            <button type="button" className="button secondary" disabled={p.saving} onClick={() => p.onSave(false)}>保存修改</button>
            <button type="button" className={`button primary ${p.edit.confirmed ? 'confirmed' : ''}`} disabled={p.saving || p.edit.confirmed} onClick={() => p.onSave(true)}>{p.saving ? '确认中…' : p.edit.confirmed ? '✓ 已确认' : '确认本镜头'}</button>
          </div>
        </form>}
      </main>}
    </div>
    <footer className="stage-footer"><div><strong>最终提示词只读取已确认的分镜事实</strong><small>重新理解会生成新的AI总结，当前人工版本继续保留。</small></div><button className="button primary large" disabled={confirmedCount !== p.shots.length} onClick={p.onOpenPrompt}>生成完整提示词</button></footer>
  </section>
}
