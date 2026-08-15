export function moveBoundary<T extends { start: number; end: number }>(shots: T[], leftIndex: number, requested: number): T[] {
  const left = shots[leftIndex]
  const right = shots[leftIndex + 1]
  if (!left || !right) return shots
  const boundary = Math.max(left.start + 0.04, Math.min(right.end - 0.04, requested))
  return shots.map((shot, index) => index === leftIndex ? { ...shot, end: boundary } : index === leftIndex + 1 ? { ...shot, start: boundary } : shot)
}
