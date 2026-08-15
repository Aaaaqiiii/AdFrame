import { useMemo, useRef } from 'react'
import type { RefObject } from 'react'
import type { TimelineShot } from '../api'
import { pointerTime, splitButtonLabel } from '../timelineScrubbing'

type Props = {
  videoUrl: string
  videoRatio: string
  videoRef: RefObject<HTMLVideoElement | null>
  shots: TimelineShot[]
  selectedIds: string[]
  selectedBoundary: number | null
  playhead: number
  fps: number
  dirty: boolean
  saving: boolean
  onMetadata: (width: number, height: number, duration: number) => void
  onPlayhead: (value: number) => void
  onSelectShot: (id: string, multi: boolean) => void
  onSelectBoundary: (index: number) => void
  onMoveBoundary: (index: number, value: number) => void
  onSplit: (atSec: number) => void
  onMerge: () => void
  onRestore: () => void
  onSave: () => void
  canContinue: boolean
  onContinue: () => void
}

const time = (value: number) => `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toFixed(2).padStart(5, '0')}`

export function TimelineEditor(p: Props) {
  const scrubbing = useRef(false)
  const resumeAfterScrub = useRef(false)
  const scrubStartX = useRef(0)
  const scrubMoved = useRef(false)
  const draggingBoundary = useRef<number | null>(null)
  const duration = p.shots.at(-1)?.end_sec || 0
  const boundary = p.selectedBoundary === null ? null : p.shots[p.selectedBoundary]?.end_sec
  const currentShot = p.shots.find((shot) => shot.start_sec < p.playhead && p.playhead < shot.end_sec)
  const currentShotNumber = currentShot ? p.shots.indexOf(currentShot) + 1 : undefined
  const range = useMemo(() => p.selectedBoundary === null ? null : {
    min: p.shots[p.selectedBoundary].start_sec + 1 / p.fps,
    max: p.shots[p.selectedBoundary + 1].end_sec - 1 / p.fps,
  }, [p.fps, p.selectedBoundary, p.shots])

  function seekFromPointer(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect()
    p.onPlayhead(pointerTime(event.clientX, box.left, box.width, duration))
  }

  function startScrub(event: React.PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('.boundary-pin')) return
    // 阻止浏览器在横向拖动时选中时间刻度和镜头文字。
    event.preventDefault()
    window.getSelection()?.removeAllRanges()
    scrubbing.current = true
    scrubMoved.current = false
    scrubStartX.current = event.clientX
    resumeAfterScrub.current = Boolean(p.videoRef.current && !p.videoRef.current.paused)
    p.videoRef.current?.pause()
    event.currentTarget.setPointerCapture(event.pointerId)
    seekFromPointer(event)
  }

  function moveScrub(event: React.PointerEvent<HTMLDivElement>) {
    if (draggingBoundary.current !== null) {
      // 边界拖动由整条时间轴接管，指针离开细切点后也不会中断。
      event.preventDefault()
      const box = event.currentTarget.getBoundingClientRect()
      p.onMoveBoundary(draggingBoundary.current, pointerTime(event.clientX, box.left, box.width, duration))
      return
    }
    if (!scrubbing.current) return
    event.preventDefault()
    if (Math.abs(event.clientX - scrubStartX.current) > 3) scrubMoved.current = true
    seekFromPointer(event)
  }

  function finishScrub(event: React.PointerEvent<HTMLDivElement>) {
    if (draggingBoundary.current !== null) {
      event.preventDefault()
      const box = event.currentTarget.getBoundingClientRect()
      p.onMoveBoundary(draggingBoundary.current, pointerTime(event.clientX, box.left, box.width, duration))
      draggingBoundary.current = null
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      return
    }
    if (!scrubbing.current) return
    seekFromPointer(event)
    scrubbing.current = false
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (resumeAfterScrub.current) void p.videoRef.current?.play()
  }

  function selectShot(id: string, event: React.MouseEvent<HTMLButtonElement>) {
    if (scrubMoved.current) { scrubMoved.current = false; return }
    p.onSelectShot(id, event.ctrlKey || event.metaKey)
  }

  function startBoundaryDrag(index: number, event: React.PointerEvent<HTMLButtonElement>) {
    event.preventDefault()
    event.stopPropagation()
    window.getSelection()?.removeAllRanges()
    draggingBoundary.current = index
    p.onSelectBoundary(index)
    // 由父级时间轴捕获指针，扩大可拖动区域并避免快速拖动时丢失事件。
    event.currentTarget.parentElement?.setPointerCapture(event.pointerId)
  }

  return <section className="stage-content timeline-stage">
    <div className="stage-heading"><div><span className="eyebrow">第二步</span><h1>先切分，再校正镜头边界</h1><p>本地工具先生成候选分镜；确认边界后，系统才会开始理解每个镜头。</p></div><span className={`save-state ${p.dirty ? 'dirty' : ''}`}>{p.dirty ? '有未保存改动' : '时间轴已保存'}</span></div>
    <div className="timeline-workspace">
      <div className="video-canvas" style={{ aspectRatio: p.videoRatio }}><video ref={p.videoRef} controls src={p.videoUrl} onTimeUpdate={(event) => p.onPlayhead(event.currentTarget.currentTime)} onSeeking={(event) => p.onPlayhead(event.currentTarget.currentTime)} onSeeked={(event) => p.onPlayhead(event.currentTarget.currentTime)} onLoadedMetadata={(event) => p.onMetadata(event.currentTarget.videoWidth, event.currentTarget.videoHeight, event.currentTarget.duration)} /></div>
      <div className="timeline-ruler"><span>{time(0)}</span><span>{time(duration / 2)}</span><span>{time(duration)}</span></div>
      <div className="timeline-track-large" aria-label="人工镜头时间轴，可拖动定位视频" onPointerDown={startScrub} onPointerMove={moveScrub} onPointerUp={finishScrub} onPointerCancel={finishScrub} onLostPointerCapture={() => { draggingBoundary.current = null }}>
        <div className="playhead-line" style={{ left: `${duration ? playheadPercent(p.playhead, duration) : 0}%` }}><span>{time(p.playhead)}</span></div>
        {p.shots.map((shot, index) => <button key={shot.id} className={`${p.selectedIds.includes(shot.id) ? 'selected' : ''} ${shot.start_sec <= p.playhead && p.playhead < shot.end_sec ? 'at-playhead' : ''}`} style={{ width: `${((shot.end_sec - shot.start_sec) / duration) * 100}%` }} onClick={(event) => selectShot(shot.id, event)}><strong>镜头 {index + 1}</strong><small>{shot.start_sec.toFixed(2)}–{shot.end_sec.toFixed(2)}s</small></button>)}
        {p.shots.slice(0, -1).map((shot, index) => <button key={`boundary-${shot.id}`} className={`boundary-pin ${p.selectedBoundary === index ? 'active' : ''}`} style={{ left: `${(shot.end_sec / duration) * 100}%` }} onPointerDown={(event) => startBoundaryDrag(index, event)} onClick={() => p.onSelectBoundary(index)} aria-label={`拖动镜头 ${index + 1} 与镜头 ${index + 2} 的边界`}><i /></button>)}
      </div>
      <input className="playhead-slider" aria-label="播放位置" type="range" min={0} max={duration} step={1 / p.fps} value={Math.min(p.playhead, duration)} onInput={(event) => p.onPlayhead(Number(event.currentTarget.value))} />
      <div className="timeline-instructions"><span><b>拖动时间轴</b> 视频画面实时跟随</span><span><b>单击镜头</b> 定位并选择当前镜头</span><span><b>Ctrl + 单击</b> 选择两个相邻镜头后合并</span><span><b>点击边界线</b> 再使用下方控制器逐帧微调</span></div>
    </div>
    {boundary !== null && range && <section className="boundary-control">
      <header><div><span className="eyebrow">当前选中边界</span><h3>镜头 {p.selectedBoundary! + 1} / 镜头 {p.selectedBoundary! + 2}</h3></div><label>精确秒数<input type="number" min={range.min} max={range.max} step={1 / p.fps} value={boundary.toFixed(2)} onChange={(event) => p.onMoveBoundary(p.selectedBoundary!, Number(event.target.value))} /></label></header>
      <div className="boundary-input-row"><button onClick={() => p.onMoveBoundary(p.selectedBoundary!, boundary - 1 / p.fps)}>前移 1 帧</button><input aria-label="选中镜头边界" type="range" min={range.min} max={range.max} step={1 / p.fps} value={boundary} onChange={(event) => p.onMoveBoundary(p.selectedBoundary!, Number(event.target.value))} /><button onClick={() => p.onMoveBoundary(p.selectedBoundary!, boundary + 1 / p.fps)}>后移 1 帧</button></div>
    </section>}
    <footer className="timeline-actions"><div><button className="button ghost" onClick={p.onRestore}>恢复候选划分</button><button className="button secondary" disabled={!currentShot} onClick={() => p.onSplit(p.playhead)}>{splitButtonLabel(p.playhead, currentShotNumber)}</button><button className="button secondary" disabled={p.selectedIds.length !== 2} onClick={p.onMerge}>合并选中镜头</button></div><div><button className="button secondary" disabled={p.saving} onClick={p.onSave}>{p.saving ? '正在保存…' : p.dirty ? '保存时间轴' : '确认当前时间轴'}</button><button className="button primary large" disabled={!p.canContinue || p.dirty || p.saving} onClick={p.onContinue}>确认并开始逐镜理解</button></div></footer>
  </section>
}

function playheadPercent(playhead: number, duration: number) { return Math.max(0, Math.min(100, playhead / duration * 100)) }
