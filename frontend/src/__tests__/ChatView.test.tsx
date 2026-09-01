import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamCallbacks } from '../api'
import ChatView from '../components/ChatView'

const mocks = vi.hoisted(() => ({
  chatStream: vi.fn(),
  resumeChat: vi.fn(),
}))

vi.mock('../api', () => ({
  chatStream: mocks.chatStream,
  resumeChat: mocks.resumeChat,
}))

const OLD_SESSION = {
  sid: 'old-session',
  title: '旧会话',
  msgs: [],
  updatedAt: 1,
}

function renderChat() {
  return render(
    <MemoryRouter initialEntries={['/chat']}>
      <ChatView />
    </MemoryRouter>,
  )
}

function createPendingStream() {
  let callbacks: StreamCallbacks | undefined
  let resolveStream: ((answer: string) => void) | undefined

  mocks.chatStream.mockImplementation(
    (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      callbacks = handlers
      return new Promise<string>((resolve) => {
        resolveStream = resolve
      })
    },
  )

  return async () => {
    await act(async () => {
      callbacks?.onDone?.({ answer: '完成的回答' })
      resolveStream?.('完成的回答')
    })
  }
}

describe('ChatView session lifecycle', () => {
  beforeEach(() => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    localStorage.clear()
    localStorage.setItem('chat_sessions', JSON.stringify([OLD_SESSION]))
    mocks.chatStream.mockReset()
    mocks.resumeChat.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('keeps a newly opened chat selected when an earlier stream completes', async () => {
    const completeStream = createPendingStream()
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByPlaceholderText('输入问题，如：对比海康威视和宝钢的风险'), '分析旧会话')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())

    await user.click(screen.getAllByRole('button', { name: '+ 新对话' })[0])
    await completeStream()

    expect(screen.getByText('AI 工作台')).toBeInTheDocument()
  })

  it('does not recreate a deleted session when its stream completes', async () => {
    const completeStream = createPendingStream()
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByPlaceholderText('输入问题，如：对比海康威视和宝钢的风险'), '分析旧会话')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())

    await user.click(screen.getByRole('button', { name: '×' }))
    await completeStream()

    expect(JSON.parse(localStorage.getItem('chat_sessions') || '[]')).toEqual([])
  })

  it('renders external supplier contact details from agent references', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onReferences?.({ items: [{ name: '华东钢材供应有限公司', website_url: 'https://steel.example.com', contact_phone: '021-12345678', contact_email: 'sales@steel.example.com' }] })
      handlers.onDone?.({ answer: '已找到候选供应商。' })
      return '已找到候选供应商。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByPlaceholderText('输入问题，如：对比海康威视和宝钢的风险'), '查找钢材供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('link', { name: '官网（待核验）' })).toHaveAttribute('href', 'https://steel.example.com')
    expect(screen.getByText('电话（待核验）：021-12345678')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '邮箱（待核验）：sales@steel.example.com' })).toHaveAttribute('href', 'mailto:sales@steel.example.com')
  })

  it('persists the completed Agent workflow summary with the answer', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onWorkflowStatus?.({ status: 'completed', stage: 'completed', message: '本轮 Agent 工作流已完成', target_suppliers: ['华东钢材供应有限公司'], sources: ['local_snapshot'], evidence_status: '证据充分', loop_exit_reason: 'evidence_sufficient' })
      handlers.onDone?.({ answer: '已完成分析。' })
      return '已完成分析。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByPlaceholderText('输入问题，如：对比海康威视和宝钢的风险'), '分析供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('当前状态：已完成')).toBeInTheDocument()
    expect(screen.getByText('Loop 退出：evidence_sufficient')).toBeInTheDocument()
  })
})
