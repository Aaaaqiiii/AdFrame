import { canSaveSegmentPlan, nearestShotBoundary, segmentDuration, segmentIssue } from '../generationSegments'
import type { GenerationSegment, GenerationSegmentInput } from '../generationSegments'
import type { TimelineShot } from '../api'

type Props = {
  planVersion: number
  segments: GenerationSegment[]
  shots: TimelineShot[]
  durationSec: number
  maxSegmentSeconds: number
  recommendedMinSeconds: number
  busy: boolean
  onAutoPlan: () => void
  onSave: (inputs: GenerationSegmentInput[]) => void
  onRestoreAuto: () => void
}

const boundaryLabel: Record<GenerationSegment['start_boundary_type'], string> = {
  video_edge: '视频边缘',
  shot_boundary: '分镜边界',
  inside_shot: '镜头内部',
}

export function GenerationSegmentsEditor(p: Props) {
  const shotBoundaries = [0, ...p.shots.map((shot) => shot.end_sec), p.durationSec]
  const canSave = canSaveSegmentPlan(p.segments, p.durationSec, p.maxSegmentSeconds, p.recommendedMinSeconds)

  function snapshotInputs(): GenerationSegmentInput[] {
    return p.segments.map((segment) => ({
      source_start_sec: segment.source_start_sec,
      source_end_sec: segment.source_end_sec,
      start_boundary_type: segment.start_boundary_type,
      end_boundary_type: segment.end_boundary_type,
      short_segment_accepted: segment.short_segment_accepted,
    }))
  }

  // 用快照构造“增加切点”后的临时方案：在最长段中点最近的合法分镜边界处切开。
  function addCut(): void {
    const longest = [...p.segments].sort((a, b) => segmentDuration(b) - segmentDuration(a))[0]
    if (!longest) return
    const mid = (longest.source_start_sec + longest.source_end_sec) / 2
    const snapped = nearestShotBoundary(mid, shotBoundaries.filter((b) => b > longest.source_start_sec + 0.001 && b < longest.source_end_sec - 0.001))
    if (snapped <= longest.source_start_sec || snapped >= longest.source_end_sec) return
    const next: GenerationSegmentInput[] = []
    for (const segment of p.segments) {
      if (segment.id === longest.id) {
        next.push({
          source_start_sec: segment.source_start_sec,
          source_end_sec: snapped,
          start_boundary_type: segment.start_boundary_type,
          end_boundary_type: segment.source_end_sec === snapped ? segment.end_boundary_type : 'shot_boundary',
          short_segment_accepted: segment.short_segment_accepted,
        })
        next.push({
          source_start_sec: snapped,
          source_end_sec: segment.source_end_sec,
          start_boundary_type: 'shot_boundary',
          end_boundary_type: segment.end_boundary_type,
          short_segment_accepted: false,
        })
      } else {
        next.push({
          source_start_sec: segment.source_start_sec,
          source_end_sec: segment.source_end_sec,
          start_boundary_type: segment.start_boundary_type,
          end_boundary_type: segment.end_boundary_type,
          short_segment_accepted: segment.short_segment_accepted,
        })
      }
    }
    p.onSave(next)
  }

  function removeCut(index: number): void {
    if (p.segments.length <= 1) return
    const next = p.segments.filter((_, i) => i !== index)
    const rebuilt: GenerationSegmentInput[] = next.map((segment, i) => ({
      source_start_sec: segment.source_start_sec,
      source_end_sec: segment.source_end_sec,
      start_boundary_type: i === 0 ? 'video_edge' : segment.start_boundary_type,
      end_boundary_type: i === next.length - 1 ? 'video_edge' : segment.end_boundary_type,
      short_segment_accepted: segment.short_segment_accepted,
    }))
    p.onSave(rebuilt)
  }

  function moveBoundary(index: number, requested: number): void {
    const next = p.segments.map((segment) => ({ ...segment }))
    const current = next[index]
    const neighbor = next[index + 1]
    if (!current || !neighbor) return
    const snapped = nearestShotBoundary(requested, shotBoundaries.filter((b) => b > current.source_start_sec + 0.001 && b < neighbor.source_end_sec - 0.001))
    if (snapped <= current.source_start_sec || snapped >= neighbor.source_end_sec) return
    current.source_end_sec = snapped
    current.end_boundary_type = 'shot_boundary'
    neighbor.source_start_sec = snapped
    neighbor.start_boundary_type = 'shot_boundary'
    p.onSave(next.map((segment) => ({
      source_start_sec: segment.source_start_sec,
      source_end_sec: segment.source_end_sec,
      start_boundary_type: segment.start_boundary_type,
      end_boundary_type: segment.end_boundary_type,
      short_segment_accepted: segment.short_segment_accepted,
    })))
  }

  return <section className="stage-content segment-stage">
    <div className="stage-heading">
      <div>
        <span className="eyebrow">生成分段</span>
        <h1>参考视频分段</h1>
        <p>按 29 秒安全上限自动划分生成片段，每段对应一次 Seedance 提交。切点默认吸附到分镜边界。</p>
      </div>
      <span className="version-badge">{p.planVersion ? `方案 v${p.planVersion}` : '尚未规划'}</span>
    </div>
    <div className="segment-actions">
      <button type="button" className="button secondary" disabled={p.busy} onClick={p.onAutoPlan}>自动规划</button>
      <button type="button" className="button secondary" disabled={p.busy || !p.segments.length} onClick={addCut}>增加切点</button>
      <button type="button" className="button secondary" disabled={p.busy || !p.segments.length} onClick={() => p.onSave(snapshotInputs())}>保存分段</button>
      <button type="button" className="button secondary" disabled={p.busy || p.planVersion === 0} onClick={p.onRestoreAuto}>恢复自动方案</button>
    </div>
    <div className="segment-list">
      {!p.segments.length && <div className="segment-empty">点击“自动规划”生成分段方案。</div>}
      {p.segments.map((segment, index) => {
        const duration = segmentDuration(segment)
        const issue = segmentIssue(segment, p.durationSec, p.maxSegmentSeconds, p.recommendedMinSeconds)
        const coveredShots = p.shots.filter((shot) => shot.start_sec < segment.source_end_sec && shot.end_sec > segment.source_start_sec)
        return <div key={segment.id} className="segment-card">
          <div className="segment-card-head">
            <strong>片段 {index + 1}</strong>
            <span>{segment.source_start_sec.toFixed(2)}s – {segment.source_end_sec.toFixed(2)}s · {duration.toFixed(2)}s</span>
            <span className="segment-boundary-badge">{boundaryLabel[segment.start_boundary_type]}</span>
            <span className="segment-boundary-badge">{boundaryLabel[segment.end_boundary_type]}</span>
            {issue && <span className="segment-issue">{issue.message}</span>}
          </div>
          <div className="segment-card-shots">
            分镜 {coveredShots.length ? coveredShots.map((shot, i) => <span key={shot.id}>{p.shots.indexOf(shot) + 1}{i < coveredShots.length - 1 ? '、' : ''}</span>) : '无'}
          </div>
          {index < p.segments.length - 1 && <div className="segment-boundary-move">
            <label>切点 <input type="number" step="0.01" min={segment.source_start_sec + 0.01} max={p.segments[index + 1].source_end_sec - 0.01} value={segment.source_end_sec} onChange={(event) => moveBoundary(index, Number(event.target.value))} /></label>
            <span>吸附到最近分镜边界</span>
            <button type="button" className="button link" onClick={() => removeCut(index)}>删除此切点</button>
          </div>}
        </div>
      })}
    </div>
    <div className="segment-save-bar">
      <span>{canSave ? '分段方案合法，可保存。' : '分段方案不合法：存在空缺、重叠、超长或未确认短段。'}</span>
      <button type="button" className="button primary" disabled={!canSave || p.busy} onClick={() => p.onSave(snapshotInputs())}>保存分段方案</button>
    </div>
  </section>
}
