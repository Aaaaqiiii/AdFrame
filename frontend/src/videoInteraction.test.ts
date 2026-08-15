import { describe, expect, it } from 'vitest'

import { aspectRatio, seekToShot } from './videoInteraction'

describe('video interaction', () => {
  it('seeks the player to the selected shot start', () => {
    const player = { currentTime: 0, play: () => Promise.resolve() }

    seekToShot(player, 8.5)

    expect(player.currentTime).toBe(8.5)
  })

  it('keeps the original video aspect ratio', () => {
    expect(aspectRatio(1080, 1920)).toBe('1080 / 1920')
  })
})
