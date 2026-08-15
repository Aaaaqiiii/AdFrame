type Props = {
  projectName: string
  mode: 'preserve_product' | 'replace_product'
  onNewProject: () => void
  onOpenHistory: () => void
  onOpenSettings: () => void
}

export function AppHeader({ projectName, mode, onNewProject, onOpenHistory, onOpenSettings }: Props) {
  return <>
    <header className="app-header">
      <a className="brand" href="/preserve-product" aria-label="广告复刻工作台首页">
        <span className="brand-mark">AF</span><span><strong>AdFrame</strong><small>广告复刻工作台</small></span>
      </a>
      <nav className="mode-navigation" aria-label="当前业务页面">
        <a className={mode === 'preserve_product' ? 'active' : ''} href="/preserve-product"><b>页面一</b> 保持原产品</a>
        <a className={mode === 'replace_product' ? 'active' : ''} href="/replace-product"><b>页面二</b> 替换产品</a>
      </nav>
      <div className="header-actions">
        <span className="current-project" title={projectName}>{projectName || '尚未创建项目'}</span>
        <button className="button ghost" onClick={onOpenHistory}>历史项目</button>
        <button className="button ghost" onClick={onOpenSettings}>设置</button>
        <button className="button secondary" onClick={onNewProject}>新建项目</button>
      </div>
    </header>
  </>
}
