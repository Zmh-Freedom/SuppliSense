import { describe, it, expect, beforeEach } from 'vitest'
import { getStoredUser, setStoredUser, clearStoredUser, isAuthenticated } from '../api'

describe('auth helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('isAuthenticated returns false when no user stored', () => {
    expect(isAuthenticated()).toBe(false)
  })

  it('setStoredUser and getStoredUser work correctly', () => {
    setStoredUser('admin', 'admin')
    const user = getStoredUser()
    expect(user).toEqual({ username: 'admin', role: 'admin' })
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
})
