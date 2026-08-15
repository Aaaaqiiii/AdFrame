import { describe, expect, it } from 'vitest'
import { pointerTime, shotIndexAtTime, splitButtonLabel } from './timelineScrubbing'

describe('timeline scrubbing', () => {
  it('renders the split action as one stable label at and between boundaries', () => {
    expect(splitButtonLabel(14.88, 5)).toBe('在 14.88s 拆分镜头 5')
    expect(splitButtonLabel(14.88)).toBe('在 14.88s 拆分')
  })
  it('maps and clamps pointer positions to video time', () => {
    expect(pointerTime(300, 100, 400, 20)).toBe(10)
    expect(pointerTime(0, 100, 400, 20)).toBe(0)
    expect(pointerTime(600, 100, 400, 20)).toBe(20)
  })

  it('keeps the selected shot synchronized with the playhead', () => {
    const shots = [{ start_sec: 0, end_sec: 2.5 }, { start_sec: 2.5, end_sec: 6.53 }]
    expect(shotIndexAtTime(shots, 0)).toBe(0)
    expect(shotIndexAtTime(shots, 4.49)).toBe(1)
    expect(shotIndexAtTime(shots, 6.53)).toBe(1)
  })
})
