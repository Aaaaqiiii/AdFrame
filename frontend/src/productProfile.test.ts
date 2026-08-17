import { afterEach, describe, expect, it, vi } from 'vitest'
import { updateProductReferenceImage } from './api'
import { inferProductProfile } from './productProfile'
import { productImageDisplayName } from './productImageLabels'

afterEach(() => vi.unstubAllGlobals())

describe('product profile', () => {
  it('deduplicates AI observations into an editable starting point', () => {
    expect(inferProductProfile([
      { productInteraction: '手持白色瓶身' },
      { productInteraction: '手持白色瓶身' },
      { observations: '蓝色瓶盖，正面 Logo' },
    ])).toBe('手持白色瓶身；蓝色瓶盖，正面 Logo')
  })

  it('does not invent a fallback when AI found no product facts', () => {
    expect(inferProductProfile([])).toBe('')
  })
})

describe('product image names', () => {
  it('prefers a custom name and otherwise resolves the saved view', () => {
    expect(productImageDisplayName('front', '瓶身正面')).toBe('瓶身正面')
    expect(productImageDisplayName('right', '')).toBe('右侧')
    expect(productImageDisplayName('unknown', '   ')).toBe('其他')
  })

  it('sends a trimmed display name to the image metadata endpoint', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ display_name: '瓶身正面' }) })
    vi.stubGlobal('fetch', fetch)

    await updateProductReferenceImage('project-1', 'product', 'asset-1', { view_label: 'front', display_name: '  瓶身正面  ' })

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8010/api/projects/project-1/reference-images/product/asset-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ view_label: 'front', display_name: '瓶身正面' }) }),
    )
  })
})
