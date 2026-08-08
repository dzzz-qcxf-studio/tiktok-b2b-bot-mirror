// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import AcquisitionJobCreator from '../components/AcquisitionJobCreator.vue'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'
import Pipeline from './Pipeline.vue'

const api = vi.hoisted(() => ({
  cancelPipelineJob: vi.fn(),
  createAcquisitionJob: vi.fn(),
  getAccounts: vi.fn(),
  getPipelineJob: vi.fn(),
  getPipelineCapabilities: vi.fn(),
  listPipelineJobs: vi.fn(),
  retryPipelineJob: vi.fn(),
}))

const messages = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('element-plus', () => ({ ElMessage: messages }))

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

async function reachStrategy(wrapper: ReturnType<typeof mountCreator>) {
  await flushPromises()
  await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
  await addTag(wrapper, 'industries', '电力基础设施')
  await addTag(wrapper, 'customer-roles', '采购负责人')
  await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
  expect(wrapper.get('[data-testid="acquisition-step-2"]').attributes('aria-current')).toBe('step')
}

async function addKeyword(
  wrapper: ReturnType<typeof mountCreator>,
  text: string,
  language = 'zh-CN',
  keywordType = 'industry',
) {
  await wrapper.get('[data-testid="acquisition-keyword-text"]').setValue(text)
  await wrapper.get('[data-testid="acquisition-keyword-language"]').setValue(language)
  await wrapper.get('[data-testid="acquisition-keyword-type"]').setValue(keywordType)
  await wrapper.get('[data-testid="acquisition-keyword-add"]').trigger('click')
}

async function reachConfirm(wrapper: ReturnType<typeof mountCreator>) {
  await reachStrategy(wrapper)
  await addKeyword(wrapper, '越南 电力 项目')
  await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
  expect(wrapper.get('[data-testid="acquisition-step-3"]').attributes('aria-current')).toBe('step')
}

function createJobResponse(payload: Record<string, any>) {
  const jobId = 'job-locked-01'
  return {
    data: {
      job: {
        id: jobId,
        triggerType: 'manual',
        scheduleId: null,
        platform: payload.platform,
        accountMode: payload.accountMode,
        accountId: payload.accountId ?? null,
        requestedStages: [...payload.stages],
        stages: [],
        configSnapshot: { ...payload.configSnapshot },
        status: 'queued',
        currentStage: '',
        priority: 0,
        retryOfJobId: null,
        errorSummary: '',
        queuedAt: '2026-08-08T12:00:00Z',
        startedAt: null,
        finishedAt: null,
        createdAt: '2026-08-08T12:00:00Z',
        updatedAt: '2026-08-08T12:00:00Z',
      },
      campaign: {
        id: 81,
        jobId,
        platform: payload.platform,
        ...payload.campaign,
        createdAt: '2026-08-08T12:00:00Z',
      },
      keywords: payload.keywords.map((keyword: Record<string, any>, index: number) => ({
        id: 101 + index,
        jobId,
        platform: payload.platform,
        ...keyword,
        status: keyword.status ?? 'new',
        usageCount: 0,
        videoCount: 0,
        relevantVideoCount: 0,
        candidateCount: 0,
        qualifiedCount: 0,
        replyCount: 0,
        businessLeadCount: 0,
        lastUsedAt: null,
        createdAt: '2026-08-08T12:00:00Z',
        updatedAt: '2026-08-08T12:00:00Z',
      })),
    },
  }
}

describe('AcquisitionJobCreator execution and target profile steps', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getPipelineCapabilities.mockResolvedValue({ data: capabilities })
    api.getAccounts.mockImplementation((platform: string) => Promise.resolve({
      data: accounts.filter(account => account.platform === platform),
    }))
    api.createAcquisitionJob.mockImplementation((payload: Record<string, any>) => (
      Promise.resolve(createJobResponse(payload))
    ))
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

  it('separates hard conditions from phase 02 verification preferences', async () => {
    const wrapper = mountCreator()
    await reachStrategy(wrapper)

    expect(wrapper.get('[data-testid="acquisition-hard-conditions"]').text()).toContain('硬性')
    expect(wrapper.get('[data-testid="acquisition-preference-conditions"]').text()).toContain('阶段 02')
    const notice = wrapper.get('[data-testid="acquisition-phase02-notice"]').text()
    expect(notice).toContain('员工数')
    expect(notice).toContain('注册资本')
    expect(notice).toContain('上市状态')
    expect(notice).toContain('阶段 01')
    expect(notice).toContain('强制淘汰')
  })

  it('requires at least one unique keyword and supports independent removal', async () => {
    const wrapper = mountCreator('en-US')
    await reachStrategy(wrapper)

    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[data-testid="acquisition-keywords-error"]').text()).toContain('keyword')

    await addKeyword(wrapper, 'Power Grid', 'en', 'industry')
    await addKeyword(wrapper, ' power grid ', 'en', 'industry')
    expect(wrapper.get('[data-testid="acquisition-keywords-error"]').text()).toContain('already added')
    expect(wrapper.findAll('[data-testid^="acquisition-keyword-remove-"]')).toHaveLength(1)

    await wrapper.get('[data-testid="acquisition-keyword-remove-0"]').trigger('click')
    expect(wrapper.findAll('[data-testid^="acquisition-keyword-remove-"]')).toHaveLength(0)
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')
    expect(wrapper.get('[data-testid="acquisition-step-2"]').attributes('aria-current')).toBe('step')
  })

  it('blocks invalid exploration budgets and keyword percentages', async () => {
    const wrapper = mountCreator()
    await reachStrategy(wrapper)
    await addKeyword(wrapper, '越南电力')

    expect(wrapper.findAll('[data-testid^="acquisition-budget-"]')).toHaveLength(7)
    await wrapper.get('[data-testid="acquisition-budget-maxKeywords"]').setValue('0')
    await wrapper.get('[data-testid="acquisition-mix-effective"]').setValue('80')
    await wrapper.get('[data-testid="acquisition-mix-new"]').setValue('30')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    expect(wrapper.get('[data-testid="acquisition-strategy-errors"]').text()).toContain('关键词数量')
    expect(wrapper.get('[data-testid="acquisition-strategy-errors"]').text()).toContain('100%')
    expect(wrapper.get('[data-testid="acquisition-step-2"]').attributes('aria-current')).toBe('step')
  })

  it('shows a complete confirmation and submits one atomic payload', async () => {
    const wrapper = mountCreator()
    await reachStrategy(wrapper)
    await wrapper.get('[data-testid="acquisition-preference-employee-count"]').setValue('10~20 人')
    await wrapper.get('[data-testid="acquisition-preference-registered-capital"]').setValue('100万~1000万')
    await wrapper.get('[data-testid="acquisition-preference-listing-status"]').setValue('unlisted')
    await addKeyword(wrapper, '越南 电力 项目')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    const summary = wrapper.get('[data-testid="acquisition-confirm-summary"]').text()
    expect(summary).toContain('douyin')
    expect(summary).toContain('CN')
    expect(summary).toContain('电力基础设施')
    expect(summary).toContain('越南 电力 项目')
    expect(summary).toContain('10~20 人')

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    await flushPromises()

    expect(api.createAcquisitionJob).toHaveBeenCalledOnce()
    expect(api.createAcquisitionJob).toHaveBeenCalledWith(expect.objectContaining({
      platform: 'douyin',
      accountMode: 'auto',
      stages: expect.arrayContaining(['collect', 'filter']),
      configSnapshot: {
        creatorSchemaVersion: '1.0',
        creatorSource: 'pipeline_ui',
        targetProfileConfigured: true,
      },
      campaign: expect.objectContaining({
        countries: ['CN'],
        industries: ['电力基础设施'],
        customerRoles: ['采购负责人'],
        preferenceConditions: expect.objectContaining({
          employeeCount: '10~20 人',
          registeredCapital: '100万~1000万',
          listingStatus: 'unlisted',
        }),
        searchBudget: expect.objectContaining({
          maxKeywords: 20,
          maxLlmCalls: 100,
        }),
        keywordMix: { effectivePercent: 70, newPercent: 30 },
      }),
      keywords: [{
        text: '越南 电力 项目',
        language: 'zh-CN',
        keywordType: 'industry',
        source: 'manual',
        status: 'new',
      }],
    }))
  })

  it('coalesces concurrent submits and keeps the server snapshot immutable', async () => {
    let resolveRequest: ((value: ReturnType<typeof createJobResponse>) => void) | undefined
    api.createAcquisitionJob.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve
    }))
    const wrapper = mountCreator()
    await reachConfirm(wrapper)

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    expect(api.createAcquisitionJob).toHaveBeenCalledOnce()

    const payload = api.createAcquisitionJob.mock.calls[0]![0]
    resolveRequest?.(createJobResponse(payload))
    await flushPromises()

    const locked = wrapper.get('[data-testid="acquisition-locked-summary"]')
    expect(locked.text()).toContain('job-locked-01')
    expect(locked.text()).toContain('电力基础设施')

    await wrapper.get('[data-testid="acquisition-previous"]').trigger('click')
    await wrapper.get('[data-testid="acquisition-previous"]').trigger('click')
    await addTag(wrapper, 'industries', '数据中心建设')
    expect(wrapper.get('[data-testid="acquisition-locked-summary"]').text()).not.toContain('数据中心建设')
  })

  it('preserves the draft and exposes a retryable error after submission fails', async () => {
    api.createAcquisitionJob.mockRejectedValueOnce(new Error('backend unavailable'))
    const wrapper = mountCreator()
    await reachConfirm(wrapper)

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="acquisition-submit-error"]').text()).toContain('backend unavailable')
    expect(wrapper.find('[data-testid="acquisition-locked-summary"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="acquisition-confirm-summary"]').text()).toContain('越南 电力 项目')

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    await flushPromises()
    expect(api.createAcquisitionJob).toHaveBeenCalledTimes(2)
  })

  it('supports arrow-key platform and account-mode selection', async () => {
    const wrapper = mountCreator()
    await flushPromises()

    await wrapper.get('[data-testid="acquisition-platform-tiktok"]').trigger('click')
    await wrapper.get('[data-testid="acquisition-platform-tiktok"]').trigger('keydown.right')
    expect(wrapper.get('[data-testid="acquisition-platform-douyin"]').attributes('aria-checked')).toBe('true')

    await wrapper.get('[data-testid="acquisition-account-auto"]').trigger('keydown.right')
    expect(wrapper.get('[data-testid="acquisition-account-specified"]').attributes('aria-checked')).toBe('true')
  })

  it('associates every strategy input with an accessible label', async () => {
    const wrapper = mountCreator()
    await reachStrategy(wrapper)

    for (const control of wrapper.findAll('input, select')) {
      const element = control.element as HTMLInputElement | HTMLSelectElement
      if (element.type === 'checkbox') continue
      const nestedLabel = element.closest('label')
      const explicitLabel = element.id
        ? wrapper.find(`label[for="${element.id}"]`).exists()
        : false
      expect(Boolean(nestedLabel) || explicitLabel, element.outerHTML).toBe(true)
    }
  })

  it('locks all navigation during submission and gives keyword removal a named target', async () => {
    let resolveRequest: ((value: ReturnType<typeof createJobResponse>) => void) | undefined
    api.createAcquisitionJob.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve
    }))
    const wrapper = mountCreator()
    await reachConfirm(wrapper)

    await wrapper.get('[data-testid="acquisition-previous"]').trigger('click')
    const remove = wrapper.get('[data-testid="acquisition-keyword-remove-0"]')
    expect(remove.attributes('aria-label')).toContain('越南 电力 项目')
    await wrapper.get('[data-testid="acquisition-next"]').trigger('click')

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    expect(wrapper.findAll('[data-testid^="acquisition-step-"]').every(step => step.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.get('[data-testid="acquisition-previous"]').attributes('disabled')).not.toBeUndefined()
    expect(wrapper.get('[data-testid="acquisition-submit"]').attributes('disabled')).not.toBeUndefined()
    expect(wrapper.get('[data-testid="acquisition-submit-status"]').attributes('role')).toBe('status')

    const payload = api.createAcquisitionJob.mock.calls[0]![0]
    resolveRequest?.(createJobResponse(payload))
    await flushPromises()
  })

  it('renders FastAPI array validation details without object coercion', async () => {
    api.createAcquisitionJob.mockRejectedValueOnce({
      response: {
        data: {
          detail: [{
            loc: ['body', 'campaign', 'countries'],
            msg: 'Country is required',
            type: 'value_error',
          }],
        },
      },
    })
    const wrapper = mountCreator()
    await reachConfirm(wrapper)

    await wrapper.get('[data-testid="acquisition-submit"]').trigger('click')
    await flushPromises()

    const error = wrapper.get('[data-testid="acquisition-submit-error"]').text()
    expect(error).toContain('campaign.countries')
    expect(error).toContain('Country is required')
    expect(error).not.toContain('[object Object]')
  })
})

describe('Pipeline acquisition creator integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('replaces the legacy form and selects the newly created job after resetting history', async () => {
    const oldPayload = {
      platform: 'douyin',
      accountMode: 'auto',
      stages: ['collect'],
      configSnapshot: {},
      campaign: {
        countries: ['CN'],
        languages: ['zh-CN'],
        industries: ['旧行业'],
        products: [],
        customerRoles: ['采购方'],
        hardConditions: {},
        preferenceConditions: {},
        excludedTargets: [],
        searchBudget: {},
        keywordMix: {},
      },
      keywords: [{ text: '旧关键词', language: 'zh-CN' }],
    }
    const createdPayload = {
      ...oldPayload,
      accountMode: 'specified',
      accountId: 11,
      campaign: { ...oldPayload.campaign, industries: ['电力基础设施'] },
      keywords: [{ text: '越南电力项目', language: 'zh-CN' }],
    }
    const oldResponse = createJobResponse(oldPayload).data
    oldResponse.job.id = 'job-old-01'
    const createdResponse = createJobResponse(createdPayload).data

    api.listPipelineJobs
      .mockResolvedValueOnce({ data: { items: [oldResponse.job], total: 11, limit: 10, offset: 0 } })
      .mockResolvedValueOnce({ data: { items: [], total: 11, limit: 10, offset: 10 } })
      .mockResolvedValue({ data: { items: [createdResponse.job], total: 12, limit: 10, offset: 0 } })
    api.getPipelineJob.mockImplementation((jobId: string) => Promise.resolve({
      data: { job: jobId === createdResponse.job.id ? createdResponse.job : oldResponse.job },
    }))

    const CreatorStub = {
      name: 'AcquisitionJobCreator',
      emits: ['accountsLoaded', 'created'],
      template: '<div data-testid="pipeline-acquisition-creator"></div>',
    }
    const i18n = createI18n({
      legacy: false,
      locale: 'zh-CN',
      fallbackLocale: 'zh-CN',
      messages: { 'zh-CN': zhCN, 'en-US': enUS },
    })
    const wrapper = mount(Pipeline, {
      global: {
        plugins: [i18n],
        stubs: { AcquisitionJobCreator: CreatorStub },
        mocks: { $router: { push: vi.fn() } },
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="pipeline-acquisition-creator"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pipeline-platform-douyin"]').exists()).toBe(false)

    await wrapper.get('.history-footer button:last-child').trigger('click')
    await flushPromises()
    expect(api.listPipelineJobs).toHaveBeenLastCalledWith({ limit: 10, offset: 10 })

    const creator = wrapper.getComponent({ name: 'AcquisitionJobCreator' })
    creator.vm.$emit('accountsLoaded', accounts)
    creator.vm.$emit('created', createdResponse)
    await flushPromises()

    expect(api.listPipelineJobs).toHaveBeenLastCalledWith({ limit: 10, offset: 0 })
    expect(api.getPipelineJob).toHaveBeenLastCalledWith('job-locked-01')
    expect(wrapper.text()).toContain('job-locked-01')
    expect(wrapper.text()).toContain('@douyin_sales')
    expect(messages.success).toHaveBeenCalledOnce()

    wrapper.unmount()
  })
})
