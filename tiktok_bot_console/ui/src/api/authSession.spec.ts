import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  handleUnauthorizedResponse,
  resolvePostLoginTarget,
} from './authSession'

describe('expired application sessions', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('clears a stale token and redirects a protected 401 to login', () => {
    localStorage.setItem('token', 'stale-token')
    localStorage.setItem('username', 'operator')
    const navigate = vi.fn()

    const handled = handleUnauthorizedResponse(
      {
        status: 401,
        requestUrl: '/api/llm/providers',
        requestToken: 'stale-token',
      },
      '/config-llm',
      navigate,
    )

    expect(handled).toBe(true)
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('username')).toBeNull()
    expect(navigate).toHaveBeenCalledWith('/login?redirect=%2Fconfig-llm')
  })

  it('does not turn an incorrect login password into a redirect loop', () => {
    const navigate = vi.fn()

    const handled = handleUnauthorizedResponse(
      { status: 401, requestUrl: '/api/auth/login' },
      '/login',
      navigate,
    )

    expect(handled).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('does not let a late old-token 401 erase a newer login', () => {
    localStorage.setItem('token', 'fresh-token')
    localStorage.setItem('username', 'new-operator')
    const navigate = vi.fn()

    const handled = handleUnauthorizedResponse(
      {
        status: 401,
        requestUrl: '/api/llm/routes',
        requestToken: 'old-token',
      },
      '/config-llm',
      navigate,
    )

    expect(handled).toBe(false)
    expect(localStorage.getItem('token')).toBe('fresh-token')
    expect(localStorage.getItem('username')).toBe('new-operator')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('returns only safe local post-login destinations', () => {
    expect(resolvePostLoginTarget('/config-llm')).toBe('/config-llm')
    expect(resolvePostLoginTarget('https://evil.example')).toBe('/dashboard')
    expect(resolvePostLoginTarget('//evil.example')).toBe('/dashboard')
    expect(resolvePostLoginTarget('/login')).toBe('/dashboard')
  })
})
