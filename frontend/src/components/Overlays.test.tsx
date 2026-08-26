import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ProjectDrawer } from './Overlays'

describe('ProjectDrawer', () => {
  it('uses the editable project name as the primary title and exposes rename', () => {
    const markup = renderToStaticMarkup(<ProjectDrawer
      open
      projects={[{ id: 'project-1', name: '发膜广告八月版', reference_video_name: 'source-video.mp4' }]}
      deletingId={null}
      onClose={vi.fn()}
      onOpen={vi.fn()}
      onDelete={vi.fn()}
      onRename={vi.fn()}
    />)

    expect(markup).toContain('<strong>发膜广告八月版</strong>')
    expect(markup).toContain('>重命名</button>')
    expect(markup).toContain('<small>source-video.mp4</small>')
  })
})
