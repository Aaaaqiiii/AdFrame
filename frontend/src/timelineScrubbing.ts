export function pointerTime(clientX: number, left: number, width: number, duration: number) {
  if (width <= 0 || duration <= 0) return 0
  return Math.max(0, Math.min(duration, ((clientX - left) / width) * duration))
}

export function shotIndexAtTime(shots: Array<{ start_sec: number; end_sec: number }>, value: number) {
  // 末尾时间属于最后一个镜头；内部边界归到右侧镜头，和视频编辑器的播放习惯一致。
  if (!shots.length) return -1
  if (value >= shots[shots.length - 1].end_sec) return shots.length - 1
  return shots.findIndex((shot) => shot.start_sec <= value && value < shot.end_sec)
}

export function splitButtonLabel(playhead: number, shotNumber?: number) {
  // 保持为单一文字节点，避免浏览器翻译扩展改写子节点后触发 React removeChild 崩溃。
  return `在 ${playhead.toFixed(2)}s 拆分${shotNumber ? `镜头 ${shotNumber}` : ''}`
}
