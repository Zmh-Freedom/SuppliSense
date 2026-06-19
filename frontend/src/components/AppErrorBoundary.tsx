import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class AppErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="flex flex-col items-center justify-center min-h-screen p-8"
          style={{ background: '#fafaf8' }}
        >
          <div
            className="rounded-2xl p-8 max-w-md text-center"
            style={{ background: '#fff', border: '1px solid #e8e8e3' }}
          >
            <h2 className="text-lg font-semibold mb-2" style={{ color: '#333' }}>
              页面加载异常
            </h2>
            <p className="text-sm mb-4" style={{ color: '#555' }}>
              {this.state.error?.message || '未知错误'}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 rounded-xl text-white cursor-pointer"
              style={{ background: '#333' }}
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
