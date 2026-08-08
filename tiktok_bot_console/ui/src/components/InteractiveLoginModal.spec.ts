// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import InteractiveLoginModal from './InteractiveLoginModal.vue'
import { canApplyLoginSnapshot } from './interactiveLoginState'
import {
  cancelLoginSession,
  createLoginSession,
  getLoginSession,
  verifyLoginSession,
} from '../api'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'

vi.mock('../api', () => ({
  createLoginSession: vi.fn(),
  getLoginSession: vi.fn(),
  verifyLoginSession: vi.fn(),
  cancelLoginSession: vi.fn(),
}))

const createMock = vi.mocked(createLoginSession)
const getMock = vi.mocked(getLoginSession)
const verifyMock = vi.mocked(verifyLoginSession)
const cancelMock = vi.mocked(cancelLoginSession)

type SessionStatus =
  | 'launching'
  | 'waiting_user'
  | 'verifying'
  | 'persisted'
  | 'confirmed'
  | 'failed'
  | 'expired'
  | 'cancelled'

function session(status: SessionStatus = 'waiting_user', overrides: Record<string, unknown> = {}) {
  return {
    token: 'session-safe-token',
    platform: 'douyin' as const,
    accountAlias: 'marketing_01',
    accountId: null,
    status,
    browserOpened: true,
    browserProvider: 'playwright',
    authenticated: status === 'confirmed',
    persisted: status === 'confirmed',
    startedAt: '2026-07-28T10:00:00Z',
    expiresAt: '2026-07-28T10:10:00Z',
    errorCode: '',
    errorMessage: '',
    ...overrides,
  }
}

function mountModal(props: {
  platform?: 'tiktok' | 'douyin'
  accountAlias?: string
  accountId?: number | null
} = {}, attachTo?: HTMLElement) {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  return mount(InteractiveLoginModal, {
    props: { platform: 'douyin', ...props },
    attachTo,
    global: { plugins: [i18n] },
  })
}

describe('InteractiveLoginModal', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.resetAllMocks()
    createMock.mockResolvedValue({ data: session() } as never)
    getMock.mockResolvedValue({ data: session() } as never)
    cancelMock.mockResolvedValue({ data: session('cancelled') } as never)
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('rejects a direct nonterminal snapshot after any terminal state', () => {
    for (const status of ['confirmed', 'failed', 'expired', 'cancelled'] as const) {
      expect(canApplyLoginSnapshot(
        { token: 'same-session', status },
        { token: 'same-session', status: 'waiting_user' },
      )).toBe(false)
    }
    expect(canApplyLoginSnapshot(
      null,
      { token: 'new-session', status: 'waiting_user' },
    )).toBe(true)
    expect(canApplyLoginSnapshot(
      { token: 'old-session', status: 'waiting_user' },
      { token: 'unexpected-session', status: 'confirmed' },
    )).toBe(false)
  })

  it('creates a session with the alias and renders manual instructions without QR artefacts', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    expect(createMock).toHaveBeenCalledWith({
      platform: 'douyin',
      accountAlias: 'marketing_01',
    })
    expect(wrapper.find('[role="dialog"][aria-modal="true"]').exists()).toBe(true)
    expect(wrapper.find('img, svg, .qr-fake, .qr-real').exists()).toBe(false)
    expect(wrapper.text()).toContain('请在已打开的浏览器中完成登录和验证')
    expect(wrapper.text()).not.toMatch(/二维码|QR Code|session-safe-token/i)
    wrapper.unmount()
  })

  it('passes an existing account id when reopening that account browser', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01', accountId: 17 })
    await flushPromises()

    expect(createMock).toHaveBeenCalledWith({
      platform: 'douyin',
      accountAlias: 'marketing_01',
      accountId: 17,
    })
    wrapper.unmount()
  })

  it('reopens a new-account session after its editable alias changes', async () => {
    createMock
      .mockResolvedValueOnce({ data: session() } as never)
      .mockResolvedValueOnce({
        data: session('waiting_user', { accountAlias: 'campaign_02' }),
      } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    const input = wrapper.get('#interactive-login-alias')
    expect(input.attributes('disabled')).toBeUndefined()
    await input.setValue('campaign_02')
    expect(wrapper.get('[data-test="verify-login"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="restart-login"]').trigger('click')
    await flushPromises()
    expect(cancelMock.mock.invocationCallOrder[0]!)
      .toBeLessThan(createMock.mock.invocationCallOrder[1]!)
    expect(createMock).toHaveBeenNthCalledWith(2, {
      platform: 'douyin',
      accountAlias: 'campaign_02',
    })
    wrapper.unmount()
  })

  it('normalizes a full-width alias before create and accepts the canonical server alias', async () => {
    createMock.mockResolvedValueOnce({
      data: session('waiting_user', { accountAlias: 'marketing_01' }),
    } as never)
    verifyMock.mockResolvedValueOnce({
      data: session('confirmed', { accountAlias: 'marketing_01' }),
    } as never)
    const wrapper = mountModal({ accountAlias: 'ｍａｒｋｅｔｉｎｇ＿０１' })
    await flushPromises()

    expect(createMock).toHaveBeenCalledWith({
      platform: 'douyin',
      accountAlias: 'marketing_01',
    })
    expect(wrapper.get<HTMLInputElement>('#interactive-login-alias').element.value)
      .toBe('marketing_01')
    expect(wrapper.get('[data-test="verify-login"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-test="verify-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('success')).toEqual([['marketing_01', 'douyin']])
    wrapper.unmount()
  })

  it('rejects an explicitly empty NFKC alias without creating a session', async () => {
    const wrapper = mountModal({ accountAlias: '\u3000' })
    await flushPromises()

    expect(createMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请输入账号标识')
    expect(wrapper.get('[data-test="verify-login"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('normalizes a changed alias prop before requiring a browser reopen', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await wrapper.setProps({ accountAlias: '　ｃａｍｐａｉｇｎ＿０２　' })
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('#interactive-login-alias').element.value)
      .toBe('campaign_02')
    expect(wrapper.find('[data-test="restart-login"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('verifies only after a user click and emits success only for confirmed', async () => {
    verifyMock
      .mockResolvedValueOnce({ data: session('waiting_user') } as never)
      .mockResolvedValueOnce({ data: session('confirmed') } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    expect(verifyMock).not.toHaveBeenCalled()
    await wrapper.get('[data-test="verify-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('success')).toBeUndefined()

    await wrapper.get('[data-test="verify-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('success')).toEqual([['marketing_01', 'douyin']])
    expect(cancelMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not let an older poll response overwrite a confirmed verification', async () => {
    vi.useFakeTimers()
    let resolvePoll!: (value: unknown) => void
    getMock.mockImplementationOnce(() => new Promise(resolve => {
      resolvePoll = resolve
    }) as never)
    verifyMock.mockResolvedValueOnce({ data: session('confirmed') } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2500)
    expect(getMock).toHaveBeenCalledWith('session-safe-token')
    await wrapper.get('[data-test="verify-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('success')).toHaveLength(1)

    resolvePoll({ data: session('waiting_user') })
    await flushPromises()
    expect(wrapper.get('[data-test="login-status"]').text()).toContain('登录状态已验证并保存')
    expect(wrapper.emitted('success')).toHaveLength(1)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('never starts another timed poll while the first poll is in flight', async () => {
    vi.useFakeTimers()
    let resolveFirst!: (value: unknown) => void
    getMock
      .mockImplementationOnce(() => new Promise(resolve => {
        resolveFirst = resolve
      }) as never)
      .mockResolvedValueOnce({ data: session('waiting_user') } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2500)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(getMock).toHaveBeenCalledTimes(1)

    resolveFirst({ data: session('waiting_user') })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2499)
    expect(getMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(getMock).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('never regresses a terminal session to a late nonterminal snapshot', async () => {
    vi.useFakeTimers()
    createMock.mockResolvedValueOnce({ data: session('confirmed') } as never)
    getMock.mockResolvedValueOnce({ data: session('waiting_user') } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    expect(wrapper.get('[data-test="login-status"]').text()).toContain('登录状态已验证并保存')
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(wrapper.get('[data-test="login-status"]').text()).toContain('登录状态已验证并保存')
    expect(wrapper.emitted('success')).toEqual([['marketing_01', 'douyin']])
    wrapper.unmount()
    expect(cancelMock).not.toHaveBeenCalled()
  })

  it('ignores a second verify click while the first verification is loading', async () => {
    let resolveVerify!: (value: unknown) => void
    verifyMock.mockImplementationOnce(() => new Promise(resolve => {
      resolveVerify = resolve
    }) as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    const verifyButton = wrapper.get('[data-test="verify-login"]')
    await verifyButton.trigger('click')
    await verifyButton.trigger('click')
    expect(verifyMock).toHaveBeenCalledTimes(1)
    expect(verifyButton.attributes('disabled')).toBeDefined()

    resolveVerify({ data: session('waiting_user') })
    await flushPromises()
    wrapper.unmount()
  })

  it('cancels the active session before closing and at most once per token', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await wrapper.get('[data-test="cancel-login"]').trigger('click')
    await flushPromises()

    expect(cancelMock).toHaveBeenCalledTimes(1)
    expect(cancelMock).toHaveBeenCalledWith('session-safe-token')
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
    await flushPromises()
    expect(cancelMock).toHaveBeenCalledTimes(1)
  })

  it('cancels the old session before creating one for a switched platform', async () => {
    createMock
      .mockResolvedValueOnce({ data: session() } as never)
      .mockResolvedValueOnce({
        data: session('waiting_user', {
          token: 'tiktok-session',
          platform: 'tiktok',
        }),
      } as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await wrapper.get('[data-test="login-platform-tiktok"]').trigger('click')
    await flushPromises()

    expect(cancelMock.mock.invocationCallOrder[0]!)
      .toBeLessThan(createMock.mock.invocationCallOrder[1]!)
    expect(createMock).toHaveBeenNthCalledWith(2, {
      platform: 'tiktok',
      accountAlias: 'marketing_01',
    })
    wrapper.unmount()
  })

  it('does not create a switched session when close wins the cancellation race', async () => {
    let resolveCancel!: (value: unknown) => void
    cancelMock.mockImplementationOnce(() => new Promise(resolve => {
      resolveCancel = resolve
    }) as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await wrapper.get('[data-test="login-platform-tiktok"]').trigger('click')
    await wrapper.get('[data-test="close-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)

    resolveCancel({ data: session('cancelled') })
    await flushPromises()
    expect(createMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('keeps an existing account bound to its original platform', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01', accountId: 17 })
    await flushPromises()

    expect(wrapper.get('[data-test="login-platform-tiktok"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('cancels a late create response after the modal was closed', async () => {
    let resolveCreate!: (value: unknown) => void
    createMock.mockImplementationOnce(() => new Promise(resolve => {
      resolveCreate = resolve
    }) as never)
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    await wrapper.get('[data-test="close-login"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)

    resolveCreate({ data: session() })
    await flushPromises()
    expect(cancelMock).toHaveBeenCalledTimes(1)
    expect(cancelMock).toHaveBeenCalledWith('session-safe-token')
    wrapper.unmount()
  })

  it('supports Escape and exposes an accessible live status', async () => {
    const wrapper = mountModal({ accountAlias: 'marketing_01' })
    await flushPromises()

    const status = wrapper.get('[data-test="login-status"]')
    expect(status.attributes('role')).toBe('status')
    expect(status.attributes('aria-live')).toBe('polite')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(cancelMock).toHaveBeenCalledWith('session-safe-token')
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('moves initial focus inside and traps focus from dialog, outside, first, and last', async () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    const wrapper = mountModal({ accountAlias: 'marketing_01' }, document.body)
    await flushPromises()

    const dialog = wrapper.get<HTMLElement>('[role="dialog"]').element
    const aliasInput = wrapper.get<HTMLElement>('#interactive-login-alias').element
    expect(document.activeElement).toBe(aliasInput)
    expect(dialog.contains(document.activeElement)).toBe(true)

    const focusable = () => [...dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )]
    let items = focusable()
    const first = items[0]!
    const last = items[items.length - 1]!

    dialog.focus()
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(first)
    outside.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true }))
    await flushPromises()
    expect(document.activeElement).toBe(last)
    first.focus()
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    last.focus()
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    verifyMock.mockResolvedValueOnce({ data: session('confirmed') } as never)
    await wrapper.get('[data-test="verify-login"]').trigger('click')
    await flushPromises()
    items = focusable()
    dialog.focus()
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(items[items.length - 1])
    wrapper.unmount()
  })

  it('focuses the close control first when an existing account alias is locked', async () => {
    const wrapper = mountModal(
      { accountAlias: 'marketing_01', accountId: 17 },
      document.body,
    )
    await flushPromises()

    expect(document.activeElement)
      .toBe(wrapper.get<HTMLElement>('[data-test="close-login"]').element)
    wrapper.unmount()
  })

  it('shows a public API error without rendering credential-shaped response fields', async () => {
    createMock.mockRejectedValueOnce({
      response: {
        data: {
          detail: {
            code: 'fingerprint_provider_unavailable',
            message: '请先配置 TikTok 指纹浏览器 Provider。',
            cookie: 'secret-cookie',
          },
        },
      },
    })
    const wrapper = mountModal({ platform: 'tiktok', accountAlias: 'marketing_01' })
    await flushPromises()

    expect(wrapper.text()).toContain('请先配置 TikTok 指纹浏览器 Provider。')
    expect(wrapper.text()).not.toContain('secret-cookie')
    expect(wrapper.find('[data-test="restart-login"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
