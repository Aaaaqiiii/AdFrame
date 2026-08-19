import type { WorkflowStage } from '../workspaceDomain'

const items: Array<{ id: WorkflowStage; number: string; label: string; note: string }> = [
  { id: 'materials', number: '01', label: '上传素材', note: '视频、产品与可选参考图' },
  { id: 'timeline', number: '02', label: '切分与校正', note: '本地切分，人工确认边界' },
  { id: 'segments', number: '03', label: '生成分段', note: '按安全上限规划生成片段' },
  { id: 'analysis', number: '04', label: '理解分镜', note: '豆包与 GPT 逐镜理解' },
  { id: 'shots', number: '05', label: '确认分镜事实', note: '修改并确认最终总结' },
  { id: 'prompt', number: '06', label: '生成提示词', note: 'GPT 整理，人工校对' },
  { id: 'generation', number: '07', label: '生成与结果', note: '分批提交并下载结果' },
]

type Props = { active: WorkflowStage; unlocked: WorkflowStage[]; onSelect: (stage: WorkflowStage) => void }

export function WorkflowRail({ active, unlocked, onSelect }: Props) {
  return <nav className="workflow-rail" aria-label="工作流程">
    {items.map((item) => {
      const enabled = unlocked.includes(item.id)
      return <button key={item.id} disabled={!enabled} className={active === item.id ? 'active' : ''} onClick={() => onSelect(item.id)}>
        <span>{item.number}</span><strong>{item.label}</strong><small>{item.note}</small>
      </button>
    })}
  </nav>
}
