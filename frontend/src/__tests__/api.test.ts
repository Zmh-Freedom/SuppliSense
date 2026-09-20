import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { getStoredUser, setStoredUser, clearStoredUser, isAuthenticated, chatStream } from '../api'

describe('auth helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('isAuthenticated returns false when no user stored', () => {
    expect(isAuthenticated()).toBe(false)
  })

  it('setStoredUser and getStoredUser work correctly', () => {
    setStoredUser('admin', 'admin')
    expect(getStoredUser()).toEqual({ username: 'admin', role: 'admin' })
  })

  it('isAuthenticated returns true after setStoredUser', () => {
    setStoredUser('analyst', 'analyst')
    expect(isAuthenticated()).toBe(true)
  })

  it('clearStoredUser removes user data', () => {
    setStoredUser('user', 'viewer')
    clearStoredUser()
    expect(getStoredUser()).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it('clears only the current account chat cache on logout', () => {
    setStoredUser('xiaoli.meng', 'purchaser')
    localStorage.setItem('chat_sessions:xiaoli.meng', 'current')
    localStorage.setItem('chat_sessions:minhao.zhou', 'other')
    clearStoredUser()
    expect(localStorage.getItem('chat_sessions:xiaoli.meng')).toBeNull()
    expect(localStorage.getItem('chat_sessions:minhao.zhou')).toBe('other')
  })
})

describe('stream authentication recovery', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('session', JSON.stringify({ username: 'tester', role: 'purchaser' }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('refreshes once and retries the SSE request after a 401', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(new Response(
        'event: done\ndata: {"answer":"已完成","status":"completed"}\n\n',
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ))
    vi.stubGlobal('fetch', fetchMock)
    const onDone = vi.fn()

    await chatStream('分析测试供应商的风险', 'session-1', { onDone })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/chat/stream')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/auth/refresh')
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/chat/stream')
    expect(onDone).toHaveBeenCalledWith({ answer: '已完成', status: 'completed' })
    expect(localStorage.getItem('session')).toContain('tester')
  })

  it('accepts CRLF and a terminal SSE line without a trailing newline', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(
      'event: done\r\ndata: {"answer":"已完成","status":"completed"}',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ))
    vi.stubGlobal('fetch', fetchMock)
    const onDone = vi.fn()

    await chatStream('分析测试供应商的风险', 'session-crlf', { onDone })

    expect(onDone).toHaveBeenCalledWith({ answer: '已完成', status: 'completed' })
  })
})
