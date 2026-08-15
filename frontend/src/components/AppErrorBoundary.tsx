import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { message: string }

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { message: error.message || '页面发生未知错误' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 保留浏览器错误信息供排查，但界面不再整页白屏。
    console.error('AdFlow page error', error, info.componentStack)
  }

  render() {
    if (!this.state.message) return this.props.children
    return <main className="app-error-fallback" role="alert">
      <h1>页面操作发生错误</h1>
      <p>{this.state.message}</p>
      <button onClick={() => window.location.reload()}>重新载入当前项目</button>
    </main>
  }
}
