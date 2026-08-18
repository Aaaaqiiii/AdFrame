import { useEffect, useRef, useState } from 'react'
import {
  canSaveSegmentPlan,
  isShortSegmentCandidate,
  mergeSegments,
  moveSegmentBoundary,
  segmentDuration,
  segmentIssue,
  segmentStructureMatches,
  shouldResetDraft,
  splitSegment,
  toggleShortSegmentAccepted,
} from '../generationSegments'
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
  selectedSegmentId: string | null
  onSelectSegment: (id: string) => void
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
  const [draft, setDraft] = useState<GenerationSegmentInput[]>([])
  const [dirty, setDirty] = useState(false)
  const [allowInsideShot, setAllowInsideShot] = useState(false)

  // 只在稳定方案身份（plan_version）变化时重建 draft；同一不可变方案的重复 GET
  // （轮询期间 restoreProject 会产生新数组引用）不得清空用户未保存草稿。
  const planIdentity = p.planVersion
  const lastPlanIdentity = useRef<number | null>(null)
  useEffect(() => {
    if (!shouldResetDraft(planIdentity, lastPlanIdentity.current)) return
    lastPlanIdentity.current = planIdentity
    setDraft(p.segments.map((segment) => ({
      source_start_sec: segment.source_start_sec,
      source_end_sec: segment.source_end_sec,
      start_boundary_type: segment.start_boundary_type,
      end_boundary_type: segment.end_boundary_type,
      short_segment_accepted: segment.short_segment_accepted,
    })))
    setDirty(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planIdentity])

  const shotBoundaries = [0, ...p.shots.map((shot) => shot.end_sec), p.durationSec]
  const canSave = canSaveSegmentPlan(draft, p.durationSec, p.maxSegmentSeconds, p.recommendedMinSeconds)
  // 草稿与后端持久化方案结构不一致（段数或边界不同）时，按旧索引选中后端 segment 不再安全。
  const structurallyDirty = dirty && !segmentStructureMatches(draft, p.segments)

  function update(next: GenerationSegmentInput[]) {
    setDraft(next)
    setDirty(true)
  }

  function addCut(): void {
    if (!draft.length) return
    const longestIndex = draft
      .map((segment, index) => ({ segment, index }))
      .sort((a, b) => segmentDuration(b.segment) - segmentDuration(a.segment))[0]?.index ?? -1
    if (longestIndex < 0) return
    const longest = draft[longestIndex]
    const mid = (longest.source_start_sec + longest.source_end_sec) / 2
    update(splitSegment(draft, longestIndex, mid, shotBoundaries, p.durationSec, allowInsideShot))
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
      <button type="button" className="button secondary" disabled={p.busy || !draft.length} onClick={addCut}>增加切点</button>
      <button type="button" className="button secondary" disabled={p.busy || p.planVersion === 0} onClick={p.onRestoreAuto}>恢复自动方案</button>
      <label className="segment-inside-toggle"><input type="checkbox" checked={allowInsideShot} onChange={(event) => setAllowInsideShot(event.target.checked)} />允许镜头内部切分</label>
    </div>
    <div className="segment-list">
      {!draft.length && <div className="segment-empty">点击“自动规划”生成分段方案。</div>}
      {draft.map((segment, index) => {
        const duration = segmentDuration(segment)
        const issue = segmentIssue(segment, p.durationSec, p.maxSegmentSeconds, p.recommendedMinSeconds)
        const coveredShots = p.shots.filter((shot) => shot.start_sec < segment.source_end_sec && shot.end_sec > segment.source_start_sec)
        const persisted = p.segments[index]
        const selected = Boolean(persisted) && p.selectedSegmentId === persisted.id
        const isShortCandidate = isShortSegmentCandidate(segment, p.durationSec, p.recommendedMinSeconds)
        const clickable = Boolean(persisted) && !structurallyDirty
        return <div key={index} className={`segment-card${selected ? ' selected' : ''}${clickable ? '' : ' not-selectable'}`} onClick={() => { if (clickable && persisted) p.onSelectSegment(persisted.id) }}>
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
          {isShortCandidate && <div className="segment-short-accept">
            <label><input type="checkbox" checked={Boolean(segment.short_segment_accepted)} onChange={() => update(toggleShortSegmentAccepted(draft, index))} />我确认保留此短段</label>
          </div>}
          {structurallyDirty && !isShortCandidate && <div className="segment-short-accept muted">请先保存草稿以继续选择此分段。</div>}
          {index < draft.length - 1 && <div className="segment-boundary-move" onClick={(event) => event.stopPropagation()}>
            <label>切点 <input type="number" step="0.01" min={segment.source_start_sec + 0.01} max={draft[index + 1].source_end_sec - 0.01} value={segment.source_end_sec} onChange={(event) => update(moveSegmentBoundary(draft, index, Number(event.target.value), shotBoundaries, p.durationSec, allowInsideShot))} /></label>
            <span>{allowInsideShot ? '允许镜头内部切分' : '吸附到最近分镜边界'}</span>
            <button type="button" className="button link" onClick={() => update(mergeSegments(draft, index, p.durationSec))}>合并此切点</button>
          </div>}
        </div>
      })}
    </div>
    <div className="segment-save-bar">
      <span>{dirty ? (canSave ? '有未保存修改，分段方案当前合法。' : '分段方案不合法：存在空缺、重叠、超长或未确认短段。') : '分段方案已是最新。'}</span>
      <button type="button" className="button primary" disabled={!dirty || !canSave || p.busy} onClick={() => p.onSave(draft)}>保存分段方案</button>
    </div>
  </section>
}
