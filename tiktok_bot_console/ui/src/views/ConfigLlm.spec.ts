// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'

import ConfigLlm from './ConfigLlm.vue'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'

const api = vi.hoisted(() => ({
  getLlmProviders: vi.fn(),
  getLlmRoutes: vi.fn(),
  getLlmUsage: vi.fn(),
  createLlmProvider: vi.fn(),
  updateLlmProvider: vi.fn(),
  deleteLlmProvider: vi.fn(),
  testLlmProvider: vi.fn(),
  updateLlmProviderSecret: vi.fn(),
  updateLlmRoute: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
  ElMessageBox: {
    confirm: vi.fn().mockResolvedValue(undefined),
  },
}))

const provider = {
  id: 'provider-1',
  name: 'deepseek',
  displayName: 'DeepSeek',
  protocol: 'openai_chat' as const,
  baseUrl: 'https://api.deepseek.com/v1',
  defaultModel: 'deepseek-chat',
  apiKeyEnv: 'DEEPSEEK_API_KEY',
  enabled: true,
  timeoutSeconds: 30,
  configured: true,
  createdAt: '2026-07-31T00:00:00',
  updatedAt: '2026-07-31T00:00:00',
}

const routeKeys = [
  'collection',
  'qualification',
  'strategy',
  'iteration',
  'default',
]

function mountPage(
  locale: 'zh-CN' | 'en-US' = 'zh-CN',
  attachTo?: HTMLElement,
) {
  const i18n = createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  return mount(ConfigLlm, {
    attachTo,
    global: {
      plugins: [i18n],
    },
  })
}

describe('ConfigLlm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getLlmProviders.mockResolvedValue({ data: [provider] })
    api.getLlmRoutes.mockResolvedValue({
      data: routeKeys.map(routeKey => ({
        routeKey,
        providers: routeKey === 'collection'
          ? [{ providerId: provider.id, priority: 10, modelOverride: null, enabled: true }]
          : [],
      })),
    })
    api.getLlmUsage.mockResolvedValue({
      data: {
        requestCount: 8,
        successCount: 7,
        failureCount: 1,
        inputTokens: 100,
        outputTokens: 50,
        totalTokens: 150,
        fallbackCount: 1,
        averageLatencyMs: 42,
      },
    })
  })

  it('loads persisted providers, five routes, and real usage', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(api.getLlmProviders).toHaveBeenCalledOnce()
    expect(api.getLlmRoutes).toHaveBeenCalledOnce()
    expect(api.getLlmUsage).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('DeepSeek')
    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).toContain('150')
    expect(wrapper.findAll('.route-card')).toHaveLength(5)
  })

  it('renders management actions from the selected English locale', async () => {
    const wrapper = mountPage('en-US')
    await flushPromises()

    expect(wrapper.text()).toContain('Add Provider')
    expect(wrapper.text()).toContain('Test connection')
    expect(wrapper.text()).toContain('Business routes')
    expect(wrapper.text()).not.toContain('测试连接')
  })

  it('blocks route editing when persisted routes fail to load', async () => {
    api.getLlmRoutes.mockRejectedValueOnce(new Error('route service unavailable'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.route-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('业务路由暂不可编辑')
    expect(api.updateLlmRoute).not.toHaveBeenCalled()
  })

  it('names route references before delete and preserves the provider after 409', async () => {
    api.deleteLlmProvider.mockRejectedValueOnce({
      response: { data: { detail: { message: 'provider is referenced' } } },
    })
    const wrapper = mountPage()
    await flushPromises()

    const deleteButton = wrapper.findAll('button').find(button => button.text() === '删除')
    await deleteButton!.trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('用户搜索'),
      expect.anything(),
      expect.anything(),
    )
    expect(api.deleteLlmProvider).toHaveBeenCalledWith(provider.id)
    expect(wrapper.findAll('.provider-card')).toHaveLength(1)
  })

  it('tests a provider through the backend and never fetches upstream', async () => {
    const upstreamFetch = vi.spyOn(globalThis, 'fetch')
    api.testLlmProvider.mockResolvedValue({
      data: { reachable: true, latencyMs: 12.5 },
    })
    const wrapper = mountPage()
    await flushPromises()

    const testButton = wrapper.findAll('button').find(button => button.text() === '测试连接')
    expect(testButton).toBeDefined()
    await testButton!.trigger('click')
    await flushPromises()

    expect(api.testLlmProvider).toHaveBeenCalledWith(provider.id)
    expect(upstreamFetch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('连接成功')
    upstreamFetch.mockRestore()
  })

  it('keeps the secret blank while editing and submits it only when replaced', async () => {
    api.updateLlmProvider.mockResolvedValue({ data: provider })
    api.updateLlmProviderSecret.mockResolvedValue({
      data: { status: 'ok', configured: true, envVar: provider.apiKeyEnv },
    })
    const wrapper = mountPage()
    await flushPromises()

    const editButton = wrapper.findAll('button').find(button => button.text() === '编辑')
    await editButton!.trigger('click')
    const secret = wrapper.get('input[type="password"]')
    expect((secret.element as HTMLInputElement).value).toBe('')

    await secret.setValue('sk-replacement')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.updateLlmProvider).toHaveBeenCalledWith(
      provider.id,
      expect.objectContaining({ defaultModel: provider.defaultModel }),
    )
    expect(api.updateLlmProviderSecret).toHaveBeenCalledWith(
      provider.id,
      'sk-replacement',
    )
  })

  it('retries a failed secret write as an update instead of recreating the provider', async () => {
    api.createLlmProvider.mockResolvedValue({ data: provider })
    api.updateLlmProvider.mockResolvedValue({ data: provider })
    api.updateLlmProviderSecret
      .mockRejectedValueOnce(new Error('secret write failed'))
      .mockResolvedValueOnce({
        data: { status: 'ok', configured: true, envVar: provider.apiKeyEnv },
      })
    const wrapper = mountPage()
    await flushPromises()

    const addButton = wrapper.findAll('button').find(button => button.text() === '添加 Provider')
    await addButton!.trigger('click')
    await wrapper.get('input[type="password"]').setValue('sk-new-provider')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.createLlmProvider).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('编辑 Provider')

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.createLlmProvider).toHaveBeenCalledOnce()
    expect(api.updateLlmProvider).toHaveBeenCalledWith(
      provider.id,
      expect.objectContaining({ name: 'deepseek' }),
    )
    expect(api.updateLlmProviderSecret).toHaveBeenCalledTimes(2)
  })

  it('saves the visible provider order as an ordered business route', async () => {
    api.updateLlmRoute.mockImplementation((routeKey, providers) =>
      Promise.resolve({ data: { routeKey, providers } }),
    )
    const wrapper = mountPage()
    await flushPromises()

    const firstRoute = wrapper.findAll('.route-card')[0]!
    const saveButton = firstRoute.findAll('button').find(button => button.text() === '保存路由')
    await saveButton!.trigger('click')
    await flushPromises()

    expect(api.updateLlmRoute).toHaveBeenCalledWith('collection', [
      expect.objectContaining({
        providerId: provider.id,
        priority: 10,
        enabled: true,
      }),
    ])
  })

  it('opens the provider editor as an immediately visible dialog', async () => {
    const wrapper = mountPage('en-US')
    await flushPromises()

    const addButton = wrapper.findAll('button').find(button => button.text() === 'Add Provider')
    await addButton!.trigger('click')

    const overlay = wrapper.get('.provider-editor-overlay')
    const dialog = wrapper.get('[role="dialog"][aria-modal="true"]')
    expect(overlay.isVisible()).toBe(true)
    expect(dialog.text()).toContain('Connect a model service')
  })

  it('traps keyboard focus in the provider dialog and restores the trigger', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const wrapper = mountPage('en-US', host)
    await flushPromises()

    const addButton = wrapper.findAll('button').find(button => button.text() === 'Add Provider')!
    ;(addButton.element as HTMLButtonElement).focus()
    await addButton.trigger('click')
    await flushPromises()

    const firstInput = wrapper.get('[data-testid="provider-display-name"]')
    expect(document.activeElement).toBe(firstInput.element)

    const lastButton = wrapper.get('[data-testid="save-provider"]')
    ;(lastButton.element as HTMLButtonElement).focus()
    await lastButton.trigger('keydown', { key: 'Tab' })
    const closeButton = wrapper.findAll('[role="dialog"] button').find(button => button.text() === 'Close')!
    expect(document.activeElement).toBe(closeButton.element)

    await closeButton.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(lastButton.element)

    await lastButton.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(document.activeElement).toBe(addButton.element)

    wrapper.unmount()
    host.remove()
  })
})
