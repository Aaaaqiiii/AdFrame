import type { AnalysisJob, TimelineShot } from '../api'
import { effectiveShotAnalysisStatus } from '../workspaceDomain'
import { StatusBadge } from './StatusBadge'

type Props = {
  shots: TimelineShot[]
  jobs: AnalysisJob[]
  onOpenShots: () => void
}

export function AnalysisStage({ shots, jobs, onOpenShots }: Props) {
  const shotJobs = jobs.filter((job) => job.kind === 'vision_shot_analysis')
  const statusFor = (shot: TimelineShot) => effectiveShotAnalysisStatus(
    shot.analysis_status,
    shotJobs.find((job) => job.shot_id === shot.id)?.status,
  )
  const completed = shots.filter((shot) => ['completed', 'succeeded'].includes(statusFor(shot))).length
  const failed = shots.filter((shot) => statusFor(shot) === 'failed').length
  const allComplete = shots.length > 0 && completed === shots.length

  return <section className="stage-content analysis-stage">
    <div className="stage-heading"><div><span className="eyebrow">第三步</span><h1>逐镜理解已确认的时间轴</h1><p>每个镜头依次经过豆包完整片段理解、GPT关键帧理解和GPT综合。</p></div><StatusBadge status={allComplete ? 'completed' : failed ? 'failed' : 'processing'} /></div>
    <div className="analysis-pipeline">
      {shots.map((shot, index) => {
        const job = shotJobs.find((item) => item.shot_id === shot.id)
        const status = effectiveShotAnalysisStatus(shot.analysis_status, job?.status)
        return <article key={shot.id}><span className="pipeline-number">{index + 1}</span><div><strong>镜头 {index + 1}</strong><p>{shot.start_sec.toFixed(2)}–{shot.end_sec.toFixed(2)} 秒</p></div><StatusBadge status={status} /></article>
      })}
    </div>
    {failed > 0 && <div className="analysis-error"><div><strong>{failed} 个镜头理解失败</strong><p>进入下一步后，可以对失败镜头单独重新理解。</p></div></div>}
    <div className={`analysis-callout ${allComplete ? 'success' : ''}`}><div><span className="eyebrow">理解进度</span><h2>{completed}/{shots.length} 个镜头已完成</h2><p>{allComplete ? '现在可以检查GPT综合后的最终分镜事实。' : '后台正在处理，你可以停留在此页面等待。'}</p></div><button className="button primary large" disabled={!allComplete && !failed} onClick={onOpenShots}>检查分镜事实</button></div>
  </section>
}
