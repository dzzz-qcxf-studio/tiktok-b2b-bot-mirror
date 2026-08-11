export type PipelinePlatform = 'tiktok' | 'douyin'

export type AccountMode = 'auto' | 'specified'

export type PipelineStageName =
  | 'collect'
  | 'filter'
  | 'strategy'
  | 'outreach'
  | 'report'
  | 'iterate'

export type PipelineJobStatus =
  | 'queued'
  | 'running'
  | 'waiting_decision'
  | 'cancelling'
  | 'cancelled'
  | 'succeeded'
  | 'partial_failed'
  | 'failed'
  | 'interrupted'

export type PipelineStageStatus =
  | 'pending'
  | 'running'
  | 'waiting_decision'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export interface CreatePipelineJobPayload {
  platform: PipelinePlatform
  accountMode: AccountMode
  accountId?: number | null
  stages: PipelineStageName[]
  configSnapshot?: Record<string, unknown>
}

export interface PipelineStage {
  id: number
  stage: PipelineStageName
  order: number
  status: PipelineStageStatus
  attempt: number
  result: Record<string, unknown>
  errorMessage: string
  startedAt: string | null
  finishedAt: string | null
}

export type PipelineJobStage = PipelineStage
export type Stage = PipelineStage

export interface PipelineJob {
  id: string
  triggerType: 'manual' | 'schedule' | 'retry' | 'legacy'
  scheduleId: number | null
  platform: PipelinePlatform
  accountMode: AccountMode
  accountId: number | null
  requestedStages: PipelineStageName[]
  stages: PipelineStage[]
  configSnapshot: Record<string, unknown>
  status: PipelineJobStatus
  currentStage: PipelineStageName | ''
  priority: number
  retryOfJobId: string | null
  errorSummary: string
  queuedAt: string | null
  startedAt: string | null
  finishedAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface PipelineJobListParams {
  platform?: PipelinePlatform
  status?: PipelineJobStatus
  limit?: number
  offset?: number
}

export interface PipelineJobResponse {
  job: PipelineJob
}

export interface PipelineJobListResponse {
  items: PipelineJob[]
  total: number
  limit: number
  offset: number
}

export type PipelineLiveEventLevel = 'debug' | 'info' | 'warning' | 'error'

export type PipelineLiveEventType =
  | 'job.lifecycle'
  | 'stage.lifecycle'
  | 'decision.lifecycle'
  | 'candidate.lifecycle'
  | 'browse.navigate'
  | 'browse.click'
  | 'browse.scroll'
  | 'browse.wait'
  | 'browse.extract'
  | 'browse.done'
  | 'browse.error'

export type PipelineLivePayloadValue =
  | string
  | number
  | boolean
  | null
  | PipelineLivePayloadValue[]
  | { [key: string]: PipelineLivePayloadValue }

export interface PipelineLiveEventPayload {
  schemaVersion: number
  [key: string]: PipelineLivePayloadValue
}

export interface PipelineLiveEvent {
  sequence: number
  jobId: string
  stage: PipelineStageName | ''
  eventType: PipelineLiveEventType
  level: PipelineLiveEventLevel
  payload: PipelineLiveEventPayload
  createdAt: string | null
}

export interface PipelineLiveMetrics {
  totalEvents: number
  browserActions: number
  videos: number
  comments: number
  candidates: number
  evidence: number
  llmCalls: number
  remainingBudget: Record<string, number>
}

export type PipelineCheckpointStatus =
  | 'pending'
  | 'resolved'
  | 'expired'
  | 'cancelled'

export type PipelineResolutionSource = 'human' | 'timeout' | 'system'

export interface PipelineDecisionContext {
  schemaVersion: number
  title?: string
  question?: string
  summary?: string
  metrics?: Record<string, number>
  warnings?: string[]
  candidateCounts?: Record<string, number>
  remainingBudget?: Record<string, number>
  defaultReason?: string
  blockingReason?: string
  manualSession?: boolean
}

export interface PipelineDecisionCheckpoint {
  id: string
  jobId: string
  stage: PipelineStageName | ''
  kind: string
  version: number
  optionKeys: string[]
  defaultOptionKey: string
  context: PipelineDecisionContext
  status: PipelineCheckpointStatus
  deadlineAt: string | null
  resolvedAt: string | null
  resolutionKey: string | null
  resolutionSource: PipelineResolutionSource | null
  createdAt: string | null
  updatedAt: string | null
}

export interface PipelineDecisionResolution {
  checkpointId: string
  jobId: string
  stage: PipelineStageName | ''
  kind: string
  optionKey: string | null
  source: PipelineResolutionSource
  status: Exclude<PipelineCheckpointStatus, 'pending'>
  resolvedAt: string | null
  deadlineAt: string | null
}

export interface ResolvePipelineCheckpointPayload {
  optionKey: string
  version: number
  reason?: string
}

export interface ResolvePipelineCheckpointResponse {
  resolution: PipelineDecisionResolution
}

export interface CompletePipelineReviewCheckpointPayload {
  version: number
  reason?: string
}

export interface PipelineLiveJobSummary {
  id: string
  platform: PipelinePlatform
  status: PipelineJobStatus
  currentStage: PipelineStageName | ''
  requestedStages: PipelineStageName[]
  startedAt: string | null
  finishedAt: string | null
  updatedAt: string | null
}

export interface PipelineLiveStageSummary {
  stage: PipelineStageName
  order: number
  status: PipelineStageStatus
  attempt: number
  startedAt: string | null
  finishedAt: string | null
}

export interface PipelineLiveResponse {
  job: PipelineLiveJobSummary
  stage: PipelineLiveStageSummary | null
  metrics: PipelineLiveMetrics
  recentEvents: PipelineLiveEvent[]
  activeCheckpoint: PipelineDecisionCheckpoint | null
  lastSequence: number
}

export interface PipelineLiveEventListParams {
  afterSequence?: number
  limit?: number
}

export interface PipelineLiveEventListResponse {
  items: PipelineLiveEvent[]
  lastSequence: number
}

export interface PipelineActiveCheckpointResponse {
  checkpoint: PipelineDecisionCheckpoint | null
}

export type PipelineLiveTransport =
  | 'connecting'
  | 'streaming'
  | 'polling'
  | 'closed'

export interface PipelineLiveSubscriptionOptions {
  afterSequence?: number
  onEvent: (event: PipelineLiveEvent) => void
  onTransportChange?: (transport: PipelineLiveTransport) => void
  onError?: (error: Error & { code?: string }) => void
}

export interface PipelineLiveSubscription {
  readonly lastSequence: number
  abort: () => void
}

export interface PipelinePlatformCapability {
  available: boolean
  providerAvailable: boolean
  provider: 'fingerprint' | 'playwright'
  code: string
  message: string
  accountCount: number
  maxConcurrency: number
}

export interface PipelineCapabilities {
  platforms: Record<PipelinePlatform, PipelinePlatformCapability>
}

export type Capabilities = PipelineCapabilities

export interface PipelineSchedulePayload {
  name: string
  platform: PipelinePlatform
  accountMode: AccountMode
  accountId?: number | null
  stages: PipelineStageName[]
  cronExpression: string
  timezone: string
  enabled: boolean
  config?: Record<string, unknown>
  configSnapshot?: Record<string, unknown>
}

export interface PipelineSchedule {
  id: number
  name: string
  platform: PipelinePlatform
  accountMode: AccountMode
  accountId: number | null
  stages: PipelineStageName[]
  cronExpression: string
  timezone: string
  enabled: boolean
  config: Record<string, unknown>
  nextRunAt: string | null
  lastRunAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export type Schedule = PipelineSchedule

export interface PipelineScheduleResponse {
  schedule: PipelineSchedule
}

export interface PipelineScheduleListResponse {
  items: PipelineSchedule[]
  total: number
}

export interface PipelineRuntimeConfigPayload {
  daily_users: number
  daily_comment_limit: number
  daily_dm_limit: number
  comment_interval_min: number
  comment_interval_max: number
  dm_interval_min: number
  dm_interval_max: number
  comment_dm_gap_hours: number
  tiktok_keywords: string[]
  douyin_max_concurrency: number
}

export interface PipelineRuntimeConfigResponse {
  status: 'ok'
  config: PipelineRuntimeConfigPayload
  restartRequired: boolean
}

export type AcquisitionKeywordStatus =
  | 'new'
  | 'testing'
  | 'effective'
  | 'cooling'
  | 'low_yield'
  | 'disabled'

export type CandidateDiscoveryStatus =
  | 'candidate'
  | 'needs_more_evidence'
  | 'obvious_irrelevant'
  | 'duplicate'
  | 'blocked'

export type CandidateQualificationStatus =
  | 'qualified'
  | 'manual_review'
  | 'need_enrichment'
  | 'rejected'

export interface AcquisitionHardConditionsPayload {
  excludedSubjects?: string[]
  requiredKeywords?: string[]
  mustBeBusinessAccount?: boolean | null
  notListed?: boolean | null
}

export interface AcquisitionPreferenceConditionsPayload {
  employeeCount?: string | null
  registeredCapital?: string | null
  listingStatus?: 'listed' | 'unlisted' | 'unknown' | null
  companyScale?: string | null
  minimumYearsEstablished?: number | null
  maximumYearsEstablished?: number | null
}

export interface AcquisitionSearchBudget {
  maxKeywords: number
  maxVideosPerKeyword: number
  maxCommentsPerVideo: number
  maxAuthorVideos: number
  maxPages: number
  maxDurationMinutes: number
  maxLlmCalls: number
}

export interface AcquisitionKeywordMix {
  effectivePercent: number
  newPercent: number
}

export interface AcquisitionCampaignPayload {
  countries?: string[]
  languages?: string[]
  industries?: string[]
  products?: string[]
  customerRoles?: string[]
  hardConditions?: AcquisitionHardConditionsPayload
  preferenceConditions?: AcquisitionPreferenceConditionsPayload
  excludedTargets?: string[]
  searchBudget?: Partial<AcquisitionSearchBudget>
  keywordMix?: Partial<AcquisitionKeywordMix>
}

export interface AcquisitionCampaign {
  id: number
  jobId: string
  platform: PipelinePlatform
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
  createdAt: string | null
}

export interface AcquisitionCampaignResponse {
  campaign: AcquisitionCampaign
}

export interface AcquisitionKeywordCreatePayload {
  text: string
  language?: string
  keywordType?: string
  source?: string
  status?: AcquisitionKeywordStatus
}

export interface AcquisitionKeywordStatsPayload {
  status?: AcquisitionKeywordStatus
  usageCount?: number
  videoCount?: number
  relevantVideoCount?: number
  candidateCount?: number
  qualifiedCount?: number
  replyCount?: number
  businessLeadCount?: number
  lastUsedAt?: string | null
}

export interface AcquisitionKeyword {
  id: number
  jobId: string
  platform: PipelinePlatform
  text: string
  language: string
  keywordType: string
  source: string
  status: AcquisitionKeywordStatus
  usageCount: number
  videoCount: number
  relevantVideoCount: number
  candidateCount: number
  qualifiedCount: number
  replyCount: number
  businessLeadCount: number
  lastUsedAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface AcquisitionKeywordResponse {
  keyword: AcquisitionKeyword
}

export interface AcquisitionKeywordListResponse {
  items: AcquisitionKeyword[]
  total: number
  limit: number
  offset: number
}

export interface CreateAcquisitionJobPayload extends CreatePipelineJobPayload {
  campaign: AcquisitionCampaignPayload
  keywords: AcquisitionKeywordCreatePayload[]
}

export interface CreateAcquisitionJobResponse {
  job: PipelineJob
  campaign: AcquisitionCampaign
  keywords: AcquisitionKeyword[]
}

export interface AcquisitionPageParams {
  limit?: number
  offset?: number
}

export interface AcquisitionStage01Summary {
  totalCandidates: number
  evidenceCount: number
  keywordCount: number
  byDiscoveryStatus: Partial<Record<CandidateDiscoveryStatus, number>>
  bySourceType: Record<string, number>
}

export interface AcquisitionStage02Summary {
  totalCandidates: number
  byQualificationStatus: Partial<Record<CandidateQualificationStatus, number>>
  pendingHumanReview: number
  averageMatchScore: number | null
  averageConfidenceScore: number | null
}

export interface AcquisitionStage01Response {
  jobId: string
  summary: AcquisitionStage01Summary
}

export interface AcquisitionStage02Response {
  jobId: string
  summary: AcquisitionStage02Summary
}

export interface CandidateAssessment {
  id: number
  labels: string[]
  matchScore: number
  confidenceScore: number
  positiveEvidence: string[]
  negativeEvidence: string[]
  missingFields: string[]
  reasoning: string
  suggestedStatus: CandidateQualificationStatus
  modelProvider: string
  modelName: string
  schemaVersion: string
  createdAt: string | null
}

export interface CandidateEvidence {
  id: number
  sourceType: string
  keywordId: number | null
  keywordText: string
  videoId: string
  videoUrl: string
  commentId: string
  commentUrl: string
  authorId: string
  authorUrl: string
  rawText: string
  translatedText: string
  relevanceScore: number | null
  completenessScore: number | null
  collectedAt: string | null
}

export interface AcquisitionCandidate {
  jobId: string
  userId: number
  platform: PipelinePlatform
  username: string
  nickname: string
  bio: string
  country: string
  followerCount: number
  profileUrl: string
  sourceStage: string
  discoveryStatus: CandidateDiscoveryStatus
  qualificationStatus: CandidateQualificationStatus
  matchScore: number | null
  confidenceScore: number | null
  labels: string[]
  priority: number
  reviewVersion: number
  manuallyConfirmedAt: string | null
  evidenceCount: number
  createdAt: string | null
  updatedAt: string | null
  evidence?: CandidateEvidence[]
  latestAssessment?: CandidateAssessment | null
}

export interface AcquisitionCandidateListParams {
  discoveryStatus?: CandidateDiscoveryStatus
  qualificationStatus?: CandidateQualificationStatus
  limit?: number
  offset?: number
}

export interface AcquisitionCandidateListResponse {
  items: AcquisitionCandidate[]
  total: number
  limit: number
  offset: number
}

export interface AcquisitionCandidateDetailResponse {
  candidate: AcquisitionCandidate
  evidence: {
    items: CandidateEvidence[]
    total: number
    limit: number
    offset: number
  }
  latestAssessment: CandidateAssessment | null
}

export interface CandidateReviewPayload {
  reviewVersion: number
  reason?: string
  labels?: string[]
  priority?: number
}

export interface CandidateLabelsPayload {
  reviewVersion: number
  labels: string[]
  reason?: string
}

export interface CandidateResponse {
  candidate: AcquisitionCandidate
}

export interface CandidateReviewAudit {
  id: number
  jobId: string
  userId: number
  action: 'approve' | 'reject' | 'request_enrichment' | 'complete_enrichment' | 'update_labels'
  beforeStatus: CandidateQualificationStatus
  afterStatus: CandidateQualificationStatus
  labelsBefore: string[]
  labelsAfter: string[]
  priorityBefore: number
  priorityAfter: number
  reason: string
  operator: string
  createdAt: string | null
}

export interface CandidateReviewAuditListResponse {
  items: CandidateReviewAudit[]
  total: number
  limit: number
  offset: number
}
