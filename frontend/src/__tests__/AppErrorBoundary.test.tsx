import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import AppErrorBoundary from '../components/AppErrorBoundary'

function BrokenComponent() {
  throw new Error('test error')
}

describe('AppErrorBoundary', () => {
  it('renders children normally', () => {
    render(
      <AppErrorBoundary>
        <div>正常内容</div>
      </AppErrorBoundary>
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
  })

  it('shows error UI when child throws', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <AppErrorBoundary>
        <BrokenComponent />
      </AppErrorBoundary>
    )
    expect(screen.getByText('页面加载异常')).toBeInTheDocument()
    expect(screen.getByText('test error')).toBeInTheDocument()
    consoleSpy.mockRestore()
  })
})
