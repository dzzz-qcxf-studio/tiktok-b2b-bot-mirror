import type {
  AccountMode,
  AcquisitionHardConditionsPayload,
  AcquisitionKeywordCreatePayload,
  AcquisitionKeywordMix,
  AcquisitionPreferenceConditionsPayload,
  AcquisitionSearchBudget,
  CreateAcquisitionJobPayload,
  PipelinePlatform,
  PipelineStageName,
} from '../types/pipeline'

export const PIPELINE_STAGE_ORDER: PipelineStageName[] = [
  'collect',
  'filter',
  'strategy',
  'outreach',
  'report',
  'iterate',
]

export const ACQUISITION_CREATOR_SNAPSHOT = Object.freeze({
  creatorSchemaVersion: '1.0',
  creatorSource: 'pipeline_ui',
  targetProfileConfigured: true,
})

export interface AcquisitionCampaignDraft {
  countries: string[]
  languages: string[]
  industries: string[]
  products: string[]
  customerRoles: string[]
  hardConditions: Required<AcquisitionHardConditionsPayload>
  preferenceConditions: Required<AcquisitionPreferenceConditionsPayload>
  excludedTargets: string[]
  searchBudget: AcquisitionSearchBudget
  keywordMix: AcquisitionKeywordMix
}

export interface AcquisitionCreatorDraft {
  platform: PipelinePlatform
  accountMode: AccountMode
  accountId: number | null
  stages: PipelineStageName[]
  campaign: AcquisitionCampaignDraft
  keywords: AcquisitionKeywordCreatePayload[]
}

export interface AcquisitionValidationError {
  field: string
  code: string
}

export interface UniqueListResult {
  items: string[]
  error: 'blank_item' | 'duplicate_item' | null
}

export interface UniqueKeywordResult {
  items: AcquisitionKeywordCreatePayload[]
  error: 'blank_keyword' | 'duplicate_keyword' | null
}

const BUDGET_RULES = [
  ['maxKeywords', 1, 100, 'budget_max_keywords_out_of_range'],
  ['maxVideosPerKeyword', 1, 100, 'budget_max_videos_per_keyword_out_of_range'],
  ['maxCommentsPerVideo', 1, 200, 'budget_max_comments_per_video_out_of_range'],
  ['maxAuthorVideos', 1, 20, 'budget_max_author_videos_out_of_range'],
  ['maxPages', 1, 100, 'budget_max_pages_out_of_range'],
  ['maxDurationMinutes', 1, 1440, 'budget_max_duration_minutes_out_of_range'],
  ['maxLlmCalls', 1, 1000, 'budget_max_llm_calls_out_of_range'],
] as const satisfies ReadonlyArray<
  readonly [keyof AcquisitionSearchBudget, number, number, string]
>

export class AcquisitionDraftValidationError extends Error {
  readonly errors: AcquisitionValidationError[]

  constructor(errors: AcquisitionValidationError[]) {
    super('acquisition draft is invalid')
    this.name = 'AcquisitionDraftValidationError'
    this.errors = errors
  }
}

export function createAcquisitionDraft(
  platform: PipelinePlatform,
): AcquisitionCreatorDraft {
  return {
    platform,
    accountMode: 'auto',
    accountId: null,
    stages: [...PIPELINE_STAGE_ORDER],
    campaign: {
      countries: platform === 'douyin' ? ['CN'] : [],
      languages: platform === 'douyin' ? ['zh-CN'] : ['en'],
      industries: [],
      products: [],
      customerRoles: [],
      hardConditions: {
        excludedSubjects: [],
        requiredKeywords: [],
        mustBeBusinessAccount: null,
        notListed: null,
      },
      preferenceConditions: {
        employeeCount: null,
        registeredCapital: null,
        listingStatus: null,
        companyScale: null,
        minimumYearsEstablished: null,
        maximumYearsEstablished: null,
      },
      excludedTargets: [],
      searchBudget: {
        maxKeywords: 20,
        maxVideosPerKeyword: 20,
        maxCommentsPerVideo: 30,
        maxAuthorVideos: 5,
        maxPages: 10,
        maxDurationMinutes: 60,
        maxLlmCalls: 100,
      },
      keywordMix: {
        effectivePercent: 70,
        newPercent: 30,
      },
    },
    keywords: [],
  }
}

export function applyPlatformDefaults(
  draft: AcquisitionCreatorDraft,
  platform: PipelinePlatform,
): AcquisitionCreatorDraft {
  const next = cloneDraft(draft)
  next.platform = platform
  next.accountId = null
  next.campaign.countries = platform === 'douyin' ? ['CN'] : []
  next.campaign.languages = platform === 'douyin' ? ['zh-CN'] : ['en']
  return next
}

export function normalizeSelectedStages(
  stages: readonly PipelineStageName[],
): PipelineStageName[] {
  const selected = new Set<PipelineStageName>(stages)
  selected.add('collect')
  return PIPELINE_STAGE_ORDER.filter(stage => selected.has(stage))
}

export function addUniqueListItem(
  items: readonly string[],
  rawItem: string,
): UniqueListResult {
  const item = normalizeText(rawItem)
  if (!item) return { items: [...items], error: 'blank_item' }
  const normalizedKey = item.toLocaleLowerCase()
  if (items.some(existing => normalizeText(existing).toLocaleLowerCase() === normalizedKey)) {
    return { items: [...items], error: 'duplicate_item' }
  }
  return { items: [...items, item], error: null }
}

export function addUniqueKeyword(
  items: readonly AcquisitionKeywordCreatePayload[],
  candidate: AcquisitionKeywordCreatePayload,
): UniqueKeywordResult {
  const text = normalizeText(candidate.text)
  if (!text) return { items: cloneKeywords(items), error: 'blank_keyword' }
  const language = normalizeText(candidate.language ?? '')
  const key = keywordKey(text, language)
  if (items.some(item => keywordKey(item.text, item.language ?? '') === key)) {
    return { items: cloneKeywords(items), error: 'duplicate_keyword' }
  }
  return {
    items: [
      ...cloneKeywords(items),
      {
        text,
        language,
        keywordType: normalizeText(candidate.keywordType ?? '') || 'industry',
        source: normalizeText(candidate.source ?? '') || 'manual',
        status: candidate.status ?? 'new',
      },
    ],
    error: null,
  }
}

export function validateExecutionScope(
  draft: AcquisitionCreatorDraft,
): AcquisitionValidationError[] {
  const errors: AcquisitionValidationError[] = []
  if (!draft.stages.includes('collect')) {
    errors.push({ field: 'stages', code: 'collect_stage_required' })
  }
  if (draft.accountMode === 'specified' && (!Number.isInteger(draft.accountId) || Number(draft.accountId) < 1)) {
    errors.push({ field: 'accountId', code: 'account_required' })
  }
  if (draft.accountMode === 'auto' && draft.accountId !== null) {
    errors.push({ field: 'accountId', code: 'auto_account_id_forbidden' })
  }
  return errors
}

export function validateTargetProfile(
  draft: AcquisitionCreatorDraft,
): AcquisitionValidationError[] {
  const errors: AcquisitionValidationError[] = []
  const campaign = draft.campaign
  if (draft.platform === 'tiktok' && campaign.countries.length === 0) {
    errors.push({ field: 'countries', code: 'country_required' })
  }
  if (
    draft.platform === 'douyin'
    && (campaign.countries.length !== 1 || campaign.countries[0] !== 'CN')
  ) {
    errors.push({ field: 'countries', code: 'douyin_country_must_be_cn' })
  }
  if (campaign.industries.length === 0) {
    errors.push({ field: 'industries', code: 'industry_required' })
  }
  if (campaign.customerRoles.length === 0) {
    errors.push({ field: 'customerRoles', code: 'customer_role_required' })
  }
  const lists: Array<[string, string[]]> = [
    ['countries', campaign.countries],
    ['languages', campaign.languages],
    ['industries', campaign.industries],
    ['products', campaign.products],
    ['customerRoles', campaign.customerRoles],
    ['excludedTargets', campaign.excludedTargets],
  ]
  for (const [field, values] of lists) {
    errors.push(...validateStringList(field, values, 50, 200))
  }
  return deduplicateErrors(errors)
}

export function validateExplorationStrategy(
  draft: AcquisitionCreatorDraft,
): AcquisitionValidationError[] {
  const errors: AcquisitionValidationError[] = []
  const campaign = draft.campaign
  for (const [key, minimum, maximum, code] of BUDGET_RULES) {
    const value = campaign.searchBudget[key]
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      errors.push({ field: 'searchBudget.' + key, code })
    }
  }

  const effective = campaign.keywordMix.effectivePercent
  const fresh = campaign.keywordMix.newPercent
  if (
    !Number.isInteger(effective)
    || !Number.isInteger(fresh)
    || effective < 0
    || effective > 100
    || fresh < 0
    || fresh > 100
  ) {
    errors.push({ field: 'keywordMix', code: 'keyword_mix_out_of_range' })
  } else if (effective + fresh !== 100) {
    errors.push({ field: 'keywordMix', code: 'keyword_mix_total_invalid' })
  }

  const preferences = campaign.preferenceConditions
  const minimumYears = preferences.minimumYearsEstablished
  const maximumYears = preferences.maximumYearsEstablished
  if (!validOptionalInteger(minimumYears, 0, 500)) {
    errors.push({
      field: 'preferenceConditions.minimumYearsEstablished',
      code: 'minimum_years_out_of_range',
    })
  }
  if (!validOptionalInteger(maximumYears, 0, 500)) {
    errors.push({
      field: 'preferenceConditions.maximumYearsEstablished',
      code: 'maximum_years_out_of_range',
    })
  }
  if (
    minimumYears !== null
    && maximumYears !== null
    && minimumYears > maximumYears
  ) {
    errors.push({
      field: 'preferenceConditions.yearsEstablished',
      code: 'year_range_invalid',
    })
  }
  const preferenceStrings: Array<[string, string | null]> = [
    ['employeeCount', preferences.employeeCount],
    ['registeredCapital', preferences.registeredCapital],
    ['companyScale', preferences.companyScale],
  ]
  for (const [field, value] of preferenceStrings) {
    if (value !== null && normalizeText(value).length > 100) {
      errors.push({
        field: 'preferenceConditions.' + field,
        code: 'preference_value_too_long',
      })
    }
  }

  errors.push(...validateStringList(
    'hardConditions.excludedSubjects',
    campaign.hardConditions.excludedSubjects,
    50,
    200,
  ))
  errors.push(...validateStringList(
    'hardConditions.requiredKeywords',
    campaign.hardConditions.requiredKeywords,
    50,
    200,
  ))

  if (draft.keywords.length === 0) {
    errors.push({ field: 'keywords', code: 'keyword_required' })
  } else if (draft.keywords.length > 100) {
    errors.push({ field: 'keywords', code: 'keyword_limit_exceeded' })
  }
  const keywordKeys = new Set<string>()
  draft.keywords.forEach((keyword, index) => {
    const text = normalizeText(keyword.text)
    const language = normalizeText(keyword.language ?? '')
    const type = normalizeText(keyword.keywordType ?? '') || 'industry'
    const source = normalizeText(keyword.source ?? '') || 'manual'
    if (!text || text.length > 300) {
      errors.push({ field: 'keywords.' + index + '.text', code: 'keyword_text_invalid' })
    }
    if (language.length > 20) {
      errors.push({ field: 'keywords.' + index + '.language', code: 'keyword_language_invalid' })
    }
    if (type.length > 50) {
      errors.push({ field: 'keywords.' + index + '.keywordType', code: 'keyword_type_invalid' })
    }
    if (source.length > 50) {
      errors.push({ field: 'keywords.' + index + '.source', code: 'keyword_source_invalid' })
    }
    const key = keywordKey(text, language)
    if (keywordKeys.has(key)) {
      errors.push({ field: 'keywords.' + index, code: 'duplicate_keyword' })
    }
    keywordKeys.add(key)
  })
  return deduplicateErrors(errors)
}

export function buildAcquisitionJobPayload(
  draft: AcquisitionCreatorDraft,
): CreateAcquisitionJobPayload {
  const errors = [
    ...validateExecutionScope(draft),
    ...validateTargetProfile(draft),
    ...validateExplorationStrategy(draft),
  ]
  if (errors.length > 0) throw new AcquisitionDraftValidationError(errors)

  const hardConditions = draft.campaign.hardConditions
  const preferences = draft.campaign.preferenceConditions
  return {
    platform: draft.platform,
    accountMode: draft.accountMode,
    ...(draft.accountMode === 'specified' ? { accountId: draft.accountId } : {}),
    stages: normalizeSelectedStages(draft.stages),
    configSnapshot: { ...ACQUISITION_CREATOR_SNAPSHOT },
    campaign: {
      countries: normalizeList(draft.campaign.countries),
      languages: normalizeList(draft.campaign.languages),
      industries: normalizeList(draft.campaign.industries),
      products: normalizeList(draft.campaign.products),
      customerRoles: normalizeList(draft.campaign.customerRoles),
      hardConditions: {
        excludedSubjects: normalizeList(hardConditions.excludedSubjects),
        requiredKeywords: normalizeList(hardConditions.requiredKeywords),
        mustBeBusinessAccount: hardConditions.mustBeBusinessAccount,
        notListed: hardConditions.notListed,
      },
      preferenceConditions: {
        employeeCount: normalizeOptionalText(preferences.employeeCount),
        registeredCapital: normalizeOptionalText(preferences.registeredCapital),
        listingStatus: preferences.listingStatus,
        companyScale: normalizeOptionalText(preferences.companyScale),
        minimumYearsEstablished: preferences.minimumYearsEstablished,
        maximumYearsEstablished: preferences.maximumYearsEstablished,
      },
      excludedTargets: normalizeList(draft.campaign.excludedTargets),
      searchBudget: { ...draft.campaign.searchBudget },
      keywordMix: { ...draft.campaign.keywordMix },
    },
    keywords: draft.keywords.map(keyword => ({
      text: normalizeText(keyword.text),
      language: normalizeText(keyword.language ?? ''),
      keywordType: normalizeText(keyword.keywordType ?? '') || 'industry',
      source: normalizeText(keyword.source ?? '') || 'manual',
      status: keyword.status ?? 'new',
    })),
  }
}

function cloneDraft(draft: AcquisitionCreatorDraft): AcquisitionCreatorDraft {
  return {
    ...draft,
    stages: [...draft.stages],
    campaign: {
      ...draft.campaign,
      countries: [...draft.campaign.countries],
      languages: [...draft.campaign.languages],
      industries: [...draft.campaign.industries],
      products: [...draft.campaign.products],
      customerRoles: [...draft.campaign.customerRoles],
      hardConditions: {
        ...draft.campaign.hardConditions,
        excludedSubjects: [...draft.campaign.hardConditions.excludedSubjects],
        requiredKeywords: [...draft.campaign.hardConditions.requiredKeywords],
      },
      preferenceConditions: { ...draft.campaign.preferenceConditions },
      excludedTargets: [...draft.campaign.excludedTargets],
      searchBudget: { ...draft.campaign.searchBudget },
      keywordMix: { ...draft.campaign.keywordMix },
    },
    keywords: cloneKeywords(draft.keywords),
  }
}

function cloneKeywords(
  keywords: readonly AcquisitionKeywordCreatePayload[],
): AcquisitionKeywordCreatePayload[] {
  return keywords.map(keyword => ({ ...keyword }))
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, ' ')
}

function normalizeOptionalText(value: string | null): string | null {
  if (value === null) return null
  return normalizeText(value) || null
}

function normalizeList(items: readonly string[]): string[] {
  return items.map(normalizeText)
}

function keywordKey(text: string, language: string): string {
  return normalizeText(text).toLocaleLowerCase()
    + '\u0000'
    + normalizeText(language).toLocaleLowerCase()
}

function validOptionalInteger(
  value: number | null,
  minimum: number,
  maximum: number,
): boolean {
  return value === null
    || (Number.isInteger(value) && value >= minimum && value <= maximum)
}

function validateStringList(
  field: string,
  values: readonly string[],
  maximumItems: number,
  maximumLength: number,
): AcquisitionValidationError[] {
  const errors: AcquisitionValidationError[] = []
  if (values.length > maximumItems) {
    errors.push({ field, code: 'list_limit_exceeded' })
  }
  const seen = new Set<string>()
  values.forEach((value, index) => {
    const normalized = normalizeText(value)
    if (!normalized || normalized.length > maximumLength) {
      errors.push({ field: field + '.' + index, code: 'list_item_invalid' })
    }
    const key = normalized.toLocaleLowerCase()
    if (seen.has(key)) {
      errors.push({ field: field + '.' + index, code: 'duplicate_item' })
    }
    seen.add(key)
  })
  return errors
}

function deduplicateErrors(
  errors: readonly AcquisitionValidationError[],
): AcquisitionValidationError[] {
  const seen = new Set<string>()
  return errors.filter((error) => {
    const key = error.field + '\u0000' + error.code
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
