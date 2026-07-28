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

    expect(screen.getByText('AI Agent')).toBeInTheDocument()
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
})
