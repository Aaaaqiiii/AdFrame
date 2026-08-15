import { describe, expect, it } from 'vitest'
import { runGenerationPreparation } from './workflow'

describe('generation workflow', () => {
  it('saves the prompt and publishes the video before submission', async () => {
    const calls: string[] = []
    const result = await runGenerationPreparation({
      promptVersion: 0,
      publicUrl: '',
      savePrompt: async () => { calls.push('prompt'); return 7 },
      publishVideo: async () => { calls.push('publish'); return 'https://asset.example/video.mp4' },
      submit: async (version) => { calls.push(`submit:${version}`); return 'generation-1' },
    })

    expect(calls).toEqual(['prompt', 'publish', 'submit:7'])
    expect(result).toBe('generation-1')
  })
})
