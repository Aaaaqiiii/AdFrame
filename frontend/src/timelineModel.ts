export type TimedShot = { id: string; start: number; end: number }

const roundToFrame = (value: number, fps: number) => Math.round(value * fps) / fps

export function moveSharedBoundary<T extends TimedShot>(shots: T[], leftIndex: number, requested: number, fps = 25): T[] {
  const left = shots[leftIndex]
  const right = shots[leftIndex + 1]
  if (!left || !right) return shots
  const frame = 1 / fps
  const boundary = roundToFrame(Math.max(left.start + frame, Math.min(right.end - frame, requested)), fps)
  return shots.map((shot, index) => index === leftIndex
    ? { ...shot, end: boundary }
    : index === leftIndex + 1
      ? { ...shot, start: boundary }
      : shot)
}

export function splitAtPlayhead<T extends TimedShot>(shots: T[], shotId: string, atSec: number, fps = 25): T[] | null {
  const index = shots.findIndex((shot) => shot.id === shotId)
  const shot = shots[index]
  const frame = 1 / fps
  if (!shot || atSec <= shot.start + frame || atSec >= shot.end - frame) return null
  const boundary = roundToFrame(atSec, fps)
  const left = { ...shot, id: `${shot.id}-left`, end: boundary }
  const right = { ...shot, id: `${shot.id}-right`, start: boundary }
  return [...shots.slice(0, index), left, right, ...shots.slice(index + 1)]
}

export function mergeAdjacentShots<T extends TimedShot>(shots: T[], selectedIds: string[]): T[] | null {
  if (selectedIds.length !== 2) return null
  const indices = selectedIds.map((id) => shots.findIndex((shot) => shot.id === id)).sort((a, b) => a - b)
  if (indices[0] < 0 || indices[1] !== indices[0] + 1) return null
  const left = shots[indices[0]]
  const right = shots[indices[1]]
  return [...shots.slice(0, indices[0]), { ...left, end: right.end }, ...shots.slice(indices[1] + 1)]
}

export function timelineIsContinuous(shots: TimedShot[], tolerance = 0.001) {
  return shots.every((shot, index) => shot.end > shot.start && (index === 0 || Math.abs(shots[index - 1].end - shot.start) <= tolerance))
}
