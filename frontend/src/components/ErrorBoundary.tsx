import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('[ErrorBoundary] 页面渲染错误:', error, errorInfo);
  }

  private handleRefresh = (): void => {
    window.location.reload();
  };

  private handleGoHome = (): void => {
    localStorage.removeItem('active_tab');
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="flex items-center justify-center bg-[#fafaf8]" style={{ height: '100%' }}>
          <div className="text-center max-w-sm px-6">
            <div className="text-5xl mb-4 text-gray-400 select-none">&#9888;</div>
            <h1 className="text-lg font-semibold text-[#333] mb-2">
              页面出错了
            </h1>
            <p className="text-sm text-gray-400 mb-8 leading-relaxed">
              抱歉，页面遇到了意外错误。请尝试刷新页面。
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={this.handleRefresh}
                className="bg-[#333] text-white rounded-lg px-5 py-2 text-sm hover:bg-[#555] transition-colors shadow-sm"
              >
                刷新页面
              </button>
              <button
                onClick={this.handleGoHome}
                className="border border-[#e8e8e3] bg-white rounded-lg px-5 py-2 text-sm text-[#555] hover:bg-[#f5f5f0] transition-colors shadow-sm"
              >
                回到首页
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
