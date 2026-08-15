type SeekablePlayer = { currentTime: number; play: () => Promise<void> }

export function seekToShot(player: SeekablePlayer, start: number) {
  player.currentTime = start
  void player.play().catch(() => undefined)
}

export function aspectRatio(width: number, height: number) {
  return width > 0 && height > 0 ? `${width} / ${height}` : '16 / 9'
}
