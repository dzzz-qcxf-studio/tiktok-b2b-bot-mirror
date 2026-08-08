import { describe, expect, it } from 'vitest'

import type { AcquisitionKeywordCreatePayload } from '../types/pipeline'
import {
  AcquisitionDraftValidationError,
  addUniqueKeyword,
  addUniqueListItem,
  applyPlatformDefaults,
  buildAcquisitionJobPayload,
  createAcquisitionDraft,
  normalizeSelectedStages,
  validateExecutionScope,
  validateExplorationStrategy,
  validateTargetProfile,
} from './acquisitionCreator'

describe('acquisition creator domain', () => {
  it('creates a Douyin draft with China, Chinese, full ordered stages, and backend budget defaults', () => {
    const draft = createAcquisitionDraft('douyin')

    expect(draft.campaign.countries).toEqual(['CN'])
    expect(draft.campaign.languages).toEqual(['zh-CN'])
    expect(draft.stages).toEqual([
      'collect',
      'filter',
      'strategy',
      'outreach',
      'report',
      'iterate',
    ])
    expect(draft.campaign.searchBudget).toEqual({
      maxKeywords: 20,
      maxVideosPerKeyword: 20,
      maxCommentsPerVideo: 30,
      maxAuthorVideos: 5,
      maxPages: 10,
      maxDurationMinutes: 60,
      maxLlmCalls: 100,
    })
    expect(draft.campaign.keywordMix).toEqual({
      effectivePercent: 70,
      newPercent: 30,
    })
  })

  it('applies platform defaults without carrying an account or overseas countries into Douyin', () => {
    const tiktokDraft = createAcquisitionDraft('tiktok')
    tiktokDraft.accountMode = 'specified'
    tiktokDraft.accountId = 99
    tiktokDraft.campaign.countries = ['VN']
    tiktokDraft.campaign.languages = ['vi']

    const douyinDraft = applyPlatformDefaults(tiktokDraft, 'douyin')
    const tiktokAgain = applyPlatformDefaults(douyinDraft, 'tiktok')

    expect(douyinDraft.platform).toBe('douyin')
    expect(douyinDraft.accountId).toBeNull()
    expect(douyinDraft.campaign.countries).toEqual(['CN'])
    expect(douyinDraft.campaign.languages).toEqual(['zh-CN'])
    expect(tiktokAgain.campaign.countries).toEqual([])
    expect(tiktokAgain.campaign.languages).toEqual(['en'])
  })

  it('keeps collect and normalizes selected stages to the canonical order', () => {
    expect(normalizeSelectedStages(['report', 'filter', 'report'])).toEqual([
      'collect',
      'filter',
      'report',
    ])
    expect(normalizeSelectedStages([])).toEqual(['collect'])
  })

  it('trims, normalizes, and rejects blank or duplicate list entries', () => {
    expect(addUniqueListItem(['Power Grid'], '  substation   contractor  ')).toEqual({
      items: ['Power Grid', 'substation contractor'],
      error: null,
    })
    expect(addUniqueListItem(['Power Grid'], ' power grid ')).toEqual({
      items: ['Power Grid'],
      error: 'duplicate_item',
    })
    expect(addUniqueListItem(['Power Grid'], '   ')).toEqual({
      items: ['Power Grid'],
      error: 'blank_item',
    })
  })

  it('deduplicates keywords by normalized text and language while preserving other languages', () => {
    const existing: AcquisitionKeywordCreatePayload[] = [{
      text: 'Power Grid',
      language: 'en',
      keywordType: 'industry',
      source: 'manual',
      status: 'new',
    }]

    expect(addUniqueKeyword(existing, {
      text: ' power   grid ',
      language: 'EN',
      keywordType: 'intent',
    }).error).toBe('duplicate_keyword')

    expect(addUniqueKeyword(existing, {
      text: 'Power Grid',
      language: 'vi',
      keywordType: 'industry',
    })).toMatchObject({
      error: null,
      items: [
        existing[0],
        {
          text: 'Power Grid',
          language: 'vi',
          keywordType: 'industry',
          source: 'manual',
          status: 'new',
        },
      ],
    })
  })

  it('validates execution scope, including specified account selection', () => {
    const draft = createAcquisitionDraft('douyin')
    expect(validateExecutionScope(draft)).toEqual([])

    draft.accountMode = 'specified'
    expect(validateExecutionScope(draft)).toContainEqual({
      field: 'accountId',
      code: 'account_required',
    })
    draft.accountId = 3
    expect(validateExecutionScope(draft)).toEqual([])
  })

  it('requires a TikTok country, industry, and customer role but fixes Douyin to China', () => {
    const tiktokDraft = createAcquisitionDraft('tiktok')
    expect(validateTargetProfile(tiktokDraft)).toEqual(expect.arrayContaining([
      { field: 'countries', code: 'country_required' },
      { field: 'industries', code: 'industry_required' },
      { field: 'customerRoles', code: 'customer_role_required' },
    ]))

    const douyinDraft = createAcquisitionDraft('douyin')
    douyinDraft.campaign.countries = ['VN']
    douyinDraft.campaign.industries = ['power infrastructure']
    douyinDraft.campaign.customerRoles = ['contractor']
    expect(validateTargetProfile(douyinDraft)).toContainEqual({
      field: 'countries',
      code: 'douyin_country_must_be_cn',
    })
  })

  it.each([
    ['maxKeywords', 0, 'budget_max_keywords_out_of_range'],
    ['maxVideosPerKeyword', 101, 'budget_max_videos_per_keyword_out_of_range'],
    ['maxCommentsPerVideo', 201, 'budget_max_comments_per_video_out_of_range'],
    ['maxAuthorVideos', 21, 'budget_max_author_videos_out_of_range'],
    ['maxPages', 101, 'budget_max_pages_out_of_range'],
    ['maxDurationMinutes', 1441, 'budget_max_duration_minutes_out_of_range'],
    ['maxLlmCalls', 1001, 'budget_max_llm_calls_out_of_range'],
  ] as const)('validates the %s backend boundary', (field, value, code) => {
    const draft = validDraft()
    draft.campaign.searchBudget[field] = value

    expect(validateExplorationStrategy(draft)).toContainEqual({
      field: `searchBudget.${field}`,
      code,
    })
  })

  it('rejects non-integer budgets, invalid year ranges, invalid keyword mix, and no keywords', () => {
    const draft = validDraft()
    draft.campaign.searchBudget.maxKeywords = 2.5
    draft.campaign.preferenceConditions.minimumYearsEstablished = 20
    draft.campaign.preferenceConditions.maximumYearsEstablished = 2
    draft.campaign.keywordMix = { effectivePercent: 80, newPercent: 30 }
    draft.keywords = []

    expect(validateExplorationStrategy(draft)).toEqual(expect.arrayContaining([
      { field: 'searchBudget.maxKeywords', code: 'budget_max_keywords_out_of_range' },
      { field: 'preferenceConditions.yearsEstablished', code: 'year_range_invalid' },
      { field: 'keywordMix', code: 'keyword_mix_total_invalid' },
      { field: 'keywords', code: 'keyword_required' },
    ]))
  })

  it('builds the complete atomic payload with hard conditions and phase-02 preferences separated', () => {
    const draft = validDraft()
    draft.accountMode = 'auto'
    draft.accountId = null
    draft.stages = ['report', 'filter', 'collect']
    draft.campaign.hardConditions.requiredKeywords = [' substation ']
    draft.campaign.hardConditions.mustBeBusinessAccount = true
    draft.campaign.preferenceConditions.employeeCount = '10-20'
    draft.campaign.preferenceConditions.registeredCapital = '100w-1000w'
    draft.campaign.preferenceConditions.listingStatus = 'unlisted'

    const payload = buildAcquisitionJobPayload(draft)

    expect(payload).toMatchObject({
      platform: 'tiktok',
      accountMode: 'auto',
      stages: ['collect', 'filter', 'report'],
      configSnapshot: {
        creatorSchemaVersion: '1.0',
        creatorSource: 'pipeline_ui',
        targetProfileConfigured: true,
      },
      campaign: {
        countries: ['VN'],
        industries: ['power infrastructure'],
        customerRoles: ['contractor'],
        hardConditions: {
          requiredKeywords: ['substation'],
          mustBeBusinessAccount: true,
        },
        preferenceConditions: {
          employeeCount: '10-20',
          registeredCapital: '100w-1000w',
          listingStatus: 'unlisted',
        },
        searchBudget: {
          maxKeywords: 20,
          maxLlmCalls: 100,
        },
        keywordMix: {
          effectivePercent: 70,
          newPercent: 30,
        },
      },
      keywords: [{
        text: 'substation contractor',
        language: 'en',
        keywordType: 'industry',
        source: 'manual',
        status: 'new',
      }],
    })
    expect(payload).not.toHaveProperty('accountId')
    expect(JSON.stringify(payload)).not.toMatch(/token|secret|cookie|authorization/i)
  })

  it('refuses to build an invalid payload instead of relying only on UI navigation', () => {
    const draft = createAcquisitionDraft('tiktok')
    expect(() => buildAcquisitionJobPayload(draft)).toThrow(AcquisitionDraftValidationError)
  })
})

function validDraft() {
  const draft = createAcquisitionDraft('tiktok')
  draft.campaign.countries = ['VN']
  draft.campaign.languages = ['en']
  draft.campaign.industries = ['power infrastructure']
  draft.campaign.customerRoles = ['contractor']
  draft.keywords = [{
    text: 'substation contractor',
    language: 'en',
    keywordType: 'industry',
    source: 'manual',
    status: 'new',
  }]
  return draft
}
