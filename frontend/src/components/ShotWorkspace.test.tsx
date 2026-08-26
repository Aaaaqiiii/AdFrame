import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { TimelineShot } from '../api'
import { draftFromShot } from '../shotDraft'
import { ShotWorkspace } from './ShotWorkspace'

const shots = [
  { id: 'shot-1', start_sec: 0, end_sec: 2, analysis_status: 'succeeded' },
  { id: 'shot-2', start_sec: 2, end_sec: 4, analysis_status: 'succeeded' },
] as TimelineShot[]

function render(selectedId: string) {
  return renderToStaticMarkup(<ShotWorkspace
    shots={shots}
    selectedId={selectedId}
    jobs={[]}
    edit={{ people: '', action: '', product: '', productInteraction: '', background: '', camera: '', lighting: '', visualStyle: '', visibleText: '', keep: '', uncertainties: '', confirmed: true }}
    saving={false}
    mode="replace_product"
    compatibility={null}
    onSelect={vi.fn()}
    onEdit={vi.fn()}
    onSave={vi.fn()}
    onDelete={vi.fn()}
    onRetry={vi.fn()}
    onAdoptAI={vi.fn()}
    onOpenPrompt={vi.fn()}
  />)
}

describe('ShotWorkspace next-shot navigation', () => {
  it('offers the next shot and marks the last shot explicitly', () => {
    expect(render('shot-1')).toContain('下一个镜头 →')
    expect(render('shot-2')).toContain('已是最后一镜头')
  })
})

describe('draftFromShot', () => {
  it('keeps deliberately cleared human fields instead of restoring AI text', () => {
    const shot = {
      id: 'shot-clear', start_sec: 0, end_sec: 1,
      people: 'AI人物',
      action: 'AI动作',
      keep_unchanged: 'AI保持项',
      edit: {
        people: '', action: '', product: '', product_interaction: '', background: '', camera: '', lighting: '',
        visual_style: '', visible_text: '', uncertainties: '', keep_unchanged: [], confirmed: false,
      },
    } as TimelineShot

    expect(draftFromShot(shot).people).toBe('')
    expect(draftFromShot(shot).action).toBe('')
    expect(draftFromShot(shot).keep).toBe('')
  })
})
