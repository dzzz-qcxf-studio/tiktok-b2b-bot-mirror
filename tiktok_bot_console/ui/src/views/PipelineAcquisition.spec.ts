// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import AcquisitionJobCreator from '../components/AcquisitionJobCreator.vue'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'

const api = vi.hoisted(() => ({
  createAcquisitionJob: vi.fn(),
  getAccounts: vi.fn(),
  getPipelineCapabilities: vi.fn(),
}))

vi.mock('../api', () => api)

const capabilities = {
  platforms: {
    tiktok: {
      available: true,
      providerAvailable: true,
      provider: 'fingerprint',
      code: '',
      message: 'Fingerprint provider ready',
      accountCount: 1,
      maxConcurrency: 1,
    },
    douyin: {
      available: true,
      providerAvailable: true,
      provider: 'playwright',
      code: '',
      message: '',
      accountCount: 1,
      maxConcurrency: 3,
    },
  },
}

const accounts = [
  {
    id: 11,
    platform: 'douyin',
    username: 'douyin_sales',
    nickname: '抖音业务号',
    status: 'logged_in',
  },
  {
    id: 22,
    platform: 'tiktok',
    username: 'tiktok_sales',
    nickname: 'TikTok Sales',
    status: 'logged_in',
  },
]

function mountCreator(locale: 'zh-CN' | 'en-US' = 'zh-CN') {
  const i18n = createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  return mount(AcquisitionJobCreator, {
    global: {
      plugins: [i18n],
    },
  })
}

async function addTag(
  wrapper: ReturnType<typeof mountCreator>,
  field: string,
  value: string,
) {
  const input = wrapper.get(`[data-testid="acquisition-${field}-input"]`)
  await input.setValue(value)
  await input.trigger('keydown.enter')
}

describe('AcquisitionJobCreator execution and target profile steps', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getPipelineCapabilities.mockResolvedValue({ data: capabilities })
    api.getAccounts.mockImplementation((platform: string) => Promise.resolve({
      data: accounts.filter(account => account.platform === platform),
    }))
  })

  it('renders four steps and keeps collect selected while allowing other stages to change', async () => {
    const wrapper = mountCreator()
    await flushPromises()

    expect(wrapper.findAll('[data-testid^="acquisition-step-"]')).toHaveLength(4)
    expect(wrapper.get('[data-testid="acquisition-step-0"]').attributes('aria-current')).toBe('step')

    const collect = wrapper.get<HTMLInputElement>('[data-testid="acquisition-stage-collect"]')
    const iterate = wrapper.get<HTMLInputElement>('[data-testid="acquisition-stage-iterate"]')
    expect(collect.element.checked).toBe(true)
    expect(collect.element.disabled).toBe(true)
    expect(iterate.element.checked).toBe(true)

    await iterate.setValue(false)
    expect(iterate.element.checked).toBe(false)
  })

  it('preserves platform, account, and provider preflight before continuing', async () => {
    const wrapper = mountCreator()
    await flushPromises()

    expect(api.getPipelineCapabilities).toHaveBeenCalledOnce()
    expect(api.getAccounts).toHaveBeenCalledWith('douyin')
    expect(wrapper.get('[data-testid="acquisition-preflight"]').text()).toContain('执行环境可用')

    await wrapper.get('[data-testid="acquisition-account-specified"]').trigger('click')
    expect(wrapper.get<HTMLSelectElement>('[data-testid="acquisition-account-select"]').element.selectedIndex).toBe(0)
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('选择')

    await wrapper.get('[data-testid="acquisition-account-select"]').setValue('11')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[data-testid="acquisition-step-1"]').attributes('aria-current')).toBe('step')
  })

  it('locks Douyin to China and requires industry and customer role', async () => {
    const wrapper = mountCreator()
    await flushPromises()
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    const country = wrapper.get<HTMLInputElement>('[data-testid="acquisition-douyin-country"]')
    expect(country.element.value).toBe('CN')
    expect(country.element.disabled).toBe(true)

    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('行业')
    expect(wrapper.get('[role="alert"]').text()).toContain('客户角色')

    await addTag(wrapper, 'industries', ' 电力基础设施 ')
    await addTag(wrapper, 'customer-roles', '采购方')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[data-testid="acquisition-step-2"]').attributes('aria-current')).toBe('step')
  })

  it('requires a TikTok country and supports adding and removing target tags', async () => {
    const wrapper = mountCreator()
    await flushPromises()

    await wrapper.get('[data-testid="acquisition-platform-tiktok"]').trigger('click')
    await flushPromises()
    expect(api.getAccounts).toHaveBeenLastCalledWith('tiktok')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    await addTag(wrapper, 'industries', 'power infrastructure')
    await addTag(wrapper, 'customer-roles', 'contractor')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('国家')

    await addTag(wrapper, 'countries', 'VN')
    await addTag(wrapper, 'products', 'transformer')
    expect(wrapper.text()).toContain('VN')
    expect(wrapper.text()).toContain('transformer')

    await wrapper.get('[data-testid="acquisition-products-remove-0"]').trigger('click')
    expect(wrapper.text()).not.toContain('transformer')

    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[data-testid="acquisition-step-2"]').attributes('aria-current')).toBe('step')
  })

  it('rejects duplicate tags with a local accessible error', async () => {
    const wrapper = mountCreator('en-US')
    await flushPromises()
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    await addTag(wrapper, 'industries', 'Power Grid')
    await addTag(wrapper, 'industries', ' power grid ')

    expect(wrapper.get('[data-testid="acquisition-industries-error"]').attributes('role')).toBe('alert')
    expect(wrapper.get('[data-testid="acquisition-industries-error"]').text()).toContain('already added')
  })
})
