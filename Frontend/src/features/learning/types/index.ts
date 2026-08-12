export type LearningRunStatus = 'created' | 'running' | 'completed' | 'failed' | 'waiting_evidence';
export type LearningWorkspaceStatus =
  | 'idle'
  | 'analyzing_scope'
  | 'waiting_scope_review'
  | 'starting_run'
  | 'running'
  | 'waiting_evidence'
  | 'completed'
  | 'failed';
export type LearningFailureKind =
  | 'backend_unavailable'
  | 'provider_unavailable'
  | 'evidence_missing'
  | 'quality_failed'
  | 'run_failed';

export interface LearningIntakeCreateRequest {
  message: string;
  preferredLanguage: string;
}

export interface LearningIntakeSupplementRequest {
  message: string;
  preferredLanguage: string;
  resourceUrls?: string[];
  deferAutoStart?: boolean;
}

export type LearningTranscriptFormat = 'srt' | 'vtt';
export type LearningResourceMode = 'automatic' | 'specified';
export type LearningResourceStatus =
  | 'idle'
  | 'validating'
  | 'registering'
  | 'registered'
  | 'failed'
  | 'revoked'
  | 'video_only';

export interface LearningTranscriptSourceSummary {
  source_id: string;
  resource_id: string;
  resource_fingerprint: string;
  provider: string;
  external_id: string;
  canonical_url: string;
  title: string;
  source_type: 'authorized' | 'srt_vtt';
  source_format: LearningTranscriptFormat;
  source_name: string;
  language: string;
  checksum_prefix: string;
  authorization_status: 'authorized';
  status: 'active' | 'stale' | 'invalid' | 'revoked';
  segment_count: number;
  start_ms: number | null;
  end_ms: number | null;
  created_at: string;
}

export interface LearningTranscriptRegistrationRequest {
  videoUrl: string;
  format: LearningTranscriptFormat;
  language: string;
  content: string;
  sourceName?: string;
}

export interface LearningTranscriptRevokeResponse {
  source_id: string;
  status: 'revoked';
}

export interface LearningResourceDraft {
  mode: LearningResourceMode;
  videoUrl: string;
  subtitleFormat: LearningTranscriptFormat;
  subtitleLanguage: string;
  subtitleFileName: string;
  inputSource: 'none' | 'file' | 'paste';
}

export interface LearningAssumption {
  id: string;
  statement: string;
  basis: string;
  sourceRef: string;
  impact: 'high' | 'medium' | 'low';
}

export interface LearningUnknown {
  id: string;
  question: string;
  whyItMatters: string;
  impact: 'high' | 'medium' | 'low';
  blocking: boolean;
  affectedFields: string[];
}

export interface LearningScope {
  artifactId: string;
  version: number;
  userGoal: string;
  targetResult: string;
  targetResultStatus?: 'explicit' | 'assumed' | 'unknown';
  currentLevel: {
    summary: string;
    knownSkills: string[];
    knownTechnologies: string[];
    uncertainAreas: string[];
    sourceRefs: string[];
  };
  contentBudget: {
    targetTotalMinutes?: number | null;
    maximumTotalMinutes?: number | null;
    maximumVideoCount?: number | null;
    maximumSegmentMinutes?: number | null;
  };
  languagePreference: {
    preferredLanguages: string[];
    acceptableLanguages: string[];
    subtitlesAcceptable: boolean;
  };
  resourcePreference: {
    preferredPlatforms: string[];
    excludedPlatforms: string[];
    preferredStyles: string[];
    freeOnly?: boolean | null;
    userSuppliedUrls: string[];
  };
  assumptions: LearningAssumption[];
  unknowns: LearningUnknown[];
  sourceRefs: string[];
  confirmed: boolean;
}

export interface LearningKnownInformation {
  field: string;
  values: string[];
  sourceRefs: string[];
}

export interface LearningScopeReview {
  knownInformation: LearningKnownInformation[];
  recommendedGaps: LearningUnknown[];
  assumptions: LearningAssumption[];
  readyForPlanning: boolean;
  highImpactGapCount: number;
  recommendationRound: number;
  autoContinueReason: string;
}

export interface LearningIntakeResponse {
  intakeId: string;
  status: 'analyzing_scope' | 'waiting_scope_review' | 'running' | 'completed' | 'failed' | 'waiting_evidence';
  scope: LearningScope;
  review: LearningScopeReview;
  runId: string | null;
}

export interface LearningRunCreateRequest {
  goal: string;
  preferences: {
    target_result: string;
    current_level: {
      summary: string;
      knownSkills: string[];
      knownTechnologies: string[];
      uncertainAreas: string[];
      sourceRefs: string[];
    };
    content_budget: {
      targetTotalMinutes?: number;
    };
    language_preference: {
      preferredLanguages: string[];
      acceptableLanguages: string[];
      subtitlesAcceptable: boolean;
    };
    resourcePreference: {
      preferredPlatforms: string[];
      excludedPlatforms: string[];
      preferredStyles: string[];
      freeOnly: boolean;
      userSuppliedUrls: string[];
    };
    confirmed: boolean;
  };
  constraints: string[];
}

export interface LearningRunCreateResponse {
  run_id: string;
}

export interface LearningRunError {
  stage: string;
  error_type: string;
  message: string;
  validator_rule: string;
  field_path: string;
}

export interface LearningRunState {
  status: LearningRunStatus;
  current_stage: string;
  completed_stages: string[];
  error: LearningRunError | null;
  intervention?: LearningEvidenceIntervention | null;
}

export interface LearningEvidenceGap {
  knowledgeId: string;
  knowledgeName: string;
  gapType: 'missing_knowledge' | 'weak_coverage' | 'unsupported_required';
  coverageStrength: 'FULL' | 'PARTIAL' | 'WEAK' | 'MISSING';
  missingOrPartialReason: string;
}

export interface LearningInterventionResource {
  id: string;
  title: string;
  canonicalUrl: string;
  availability: string;
}

export interface LearningInterventionSegment {
  id: string;
  resourceId: string;
  startSeconds: number;
  endSeconds: number;
  contentSummary: string;
}

export interface LearningInterventionKnowledge {
  id: string;
  name: string;
  importance: 'required' | 'important' | 'optional';
  coverageStrength: 'FULL' | 'PARTIAL' | 'WEAK' | 'MISSING';
}

export interface LearningEvidenceIntervention {
  kind: 'additional_evidence_required';
  requiredGaps: LearningEvidenceGap[];
  searchedResources: string[];
  transcriptUnavailableResources: string[];
  verifiedResources: LearningInterventionResource[];
  verifiedSegments: LearningInterventionSegment[];
  knowledgeCoverage: LearningInterventionKnowledge[];
  canResume: boolean;
}

export interface LearningProgressEvent {
  stream_id?: string;
  event_type:
    | 'session_created'
    | 'stage_started'
    | 'artifact_saved'
    | 'stage_completed'
    | 'session_completed'
    | 'session_failed'
    | 'session_waiting_evidence';
  stage: string;
  status: 'created' | 'started' | 'saved' | 'completed' | 'failed' | 'waiting_evidence';
  message: string;
  timestamp: string;
}

export interface LearningArtifactRef {
  artifactType: string;
  artifactId: string;
  version: number;
}

export interface LearningCoverageGap {
  knowledgeId: string;
  reason: string;
  impact: 'blocker' | 'major' | 'minor';
  searchedResourceRefs: string[];
}

export interface LearningSelectionOmission {
  knowledgeId: string;
  importance: 'important' | 'optional';
  reason: 'budget_limit' | 'lower_priority' | 'not_required_by_scope';
  candidateSegmentRefs: string[];
  marginalDurationSeconds: number;
  policyRuleRefs: string[];
  description: string;
}

export interface LearningSelectionFacts {
  knowledgeCovered: string[];
  evidenceLevel: 'transcript' | 'caption' | 'chapter' | 'manual' | 'metadata';
  savedMinutes: number;
  versionCompatible: boolean;
  selectionRuleRefs: string[];
}

export interface LearningRecommendedContent {
  selectionId: string;
  resourceId: string;
  segmentId: string;
  videoTitle: string;
  segmentSummary: string;
  durationSeconds: number;
  recommendationReason: string;
  selectionFacts?: LearningSelectionFacts | null;
}

export interface LearningContentItem {
  knowledgeId: string;
  knowledgeName: string;
  knowledgeExplanation: string;
  whyRequired: string;
  recommendedContent: LearningRecommendedContent[];
  uncoveredReason?: string | null;
}

export interface LearningContentPlan {
  artifactId: string;
  version: number;
  items: LearningContentItem[];
  totalDurationSeconds: number;
  evidenceGaps: LearningCoverageGap[];
  deferredKnowledge: LearningSelectionOmission[];
}

export interface LearningQualityCheck {
  rule: string;
  passed: boolean;
  evidence: string[];
}

export interface LearningQualityIssue {
  issueId: string;
  rule: string;
  severity: 'blocker' | 'major' | 'minor';
  description: string;
}

export interface LearningQualityReport {
  artifactId: string;
  version: number;
  hardRulesPassed: boolean;
  qualityChecks: LearningQualityCheck[];
  issues: LearningQualityIssue[];
  remainingGaps: LearningCoverageGap[];
  score?: number | null;
  passed: boolean;
}

export interface LearningVideoResource {
  id: string;
  provider: string;
  externalId: string;
  canonicalUrl: string;
  title: string;
  author: string;
  language: string;
  durationSeconds: number;
  availability: string;
}

export interface LearningContentSegment {
  id: string;
  resourceId: string;
  startSeconds: number;
  endSeconds: number;
  contentSummary: string;
  topics: string[];
  evidenceRefs: string[];
}

export interface LearningSegmentEvidence {
  id: string;
  resourceId: string;
  segmentId: string;
  kind: string;
  supportedClaim: string;
  verificationStatus: 'verified' | 'unverified' | 'rejected';
}

export interface LearningEvidenceGraph {
  artifactId: string;
  version: number;
  resources: LearningVideoResource[];
  segments: LearningContentSegment[];
  evidence: LearningSegmentEvidence[];
}

export interface LearningRunResult {
  learning_content_plan: LearningContentPlan;
  learning_quality_report: LearningQualityReport;
  evidence_graph: LearningEvidenceGraph;
}

export interface LearningWorkspaceState {
  intakeId: string | null;
  scope: LearningScope | null;
  scopeReview: LearningScopeReview | null;
  intakeStatus: LearningWorkspaceStatus;
  supplementDraft: string;
  runId: string | null;
  status: LearningWorkspaceStatus;
  currentStage: string;
  completedStages: string[];
  events: LearningProgressEvent[];
  plan: LearningContentPlan | null;
  qualityReport: LearningQualityReport | null;
  evidenceGraph: LearningEvidenceGraph | null;
  intervention: LearningEvidenceIntervention | null;
  originalInput: string;
  preferredLanguage: string;
  scopeAnalysisFailed: boolean;
  failureKind: LearningFailureKind | null;
  resourceDraft: LearningResourceDraft;
  registeredTranscript: LearningTranscriptSourceSummary | null;
  resourceStatus: LearningResourceStatus;
  resourceError: 'video_invalid' | 'binding_failed' | 'registration_failed' | 'revoke_failed' | null;
}
