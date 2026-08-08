// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import ConfigAccounts from './ConfigAccounts.vue'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'

const api = vi.hoisted(() => ({
  getAccounts: vi.fn(),
  getPipelineCapabilities: vi.fn(),
  deleteAccount: vi.fn(),
  checkAccountSession: vi.fn(),
  updateAccountCookies: vi.fn(),
  updateAccountMetadata: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn().mockResolvedValue(undefined),
    prompt: vi.fn(),
    alert: vi.fn(),
  },
}))

const account = {
  id: 7,
  platform: 'douyin',
  username: 'sales-browser-01',
  display_name: '华南销售号',
  nickname: '真实抖音昵称',
  avatar_url: 'https://p3.douyinpic.com/avatar.jpeg',
  status: 'logged_in',
  statusKey: 'on',
  login_method: 'interactive_browser',
  last_login_at: '2026-08-01T08:30:00',
  followers: 21,
  videos: 0,
  likes: 0,
  today: null,
  browser_provider: 'playwright',
  browser_profile_id: 'profile-7',
}

function mountPage() {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh-CN',
    fallbackLocale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  return mount(ConfigAccounts, {
    global: {
      plugins: [i18n],
      stubs: { InteractiveLoginModal: true },
    },
  })
}

describe('ConfigAccounts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getAccounts.mockResolvedValue({ data: [account] })
    api.getPipelineCapabilities.mockResolvedValue({
      data: {
        platforms: {
          douyin: { available: true, providerAvailable: true, provider: 'playwright', code: '', message: '', accountCount: 1, maxConcurrency: 1 },
          tiktok: { available: false, providerAvailable: false, provider: 'fingerprint', code: '', message: '', accountCount: 0, maxConcurrency: 0 },
        },
      },
    })
  })

  it('shows the editable local display name and real platform avatar', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('华南销售号')
    expect(wrapper.text()).toContain('真实抖音昵称')
    const avatar = wrapper.get('img.account-avatar')
    expect(avatar.attributes('src')).toBe(account.avatar_url)
    expect(avatar.attributes('referrerpolicy')).toBe('no-referrer')
  })

  it('edits the local display name without changing the browser alias', async () => {
    api.updateAccountMetadata.mockResolvedValue({
      data: { ...account, display_name: '重点客户号' },
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('[data-testid="edit-account-7"]').trigger('click')
    const input = wrapper.get('[data-testid="account-display-name-input"]')
    await input.setValue('重点客户号')
    await wrapper.get('[data-testid="save-account-display-name"]').trigger('click')
    await flushPromises()

    expect(api.updateAccountMetadata).toHaveBeenCalledWith(7, '重点客户号')
    expect(api.updateAccountMetadata).not.toHaveBeenCalledWith(7, 'sales-browser-01')
  })
})
