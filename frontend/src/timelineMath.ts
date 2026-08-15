export function timelineDuration(shots: Array<{ end: number }>) {
  return shots.reduce((duration, shot) => Math.max(duration, shot.end), 0)
}
