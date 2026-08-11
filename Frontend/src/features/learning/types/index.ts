export type LearningRunStatus = 'created' | 'running' | 'completed' | 'failed';
export type LearningWorkspaceStatus = 'idle' | 'creating' | LearningRunStatus;
export type LearningFailureKind =
  | 'backend_unavailable'
  | 'provider_unavailable'
  | 'evidence_missing'
  | 'quality_failed'
  | 'run_failed';

export interface LearningWorkspaceInput {
  goal: string;
  targetResult: string;
  currentLevel: string;
  targetMinutes?: number;
  preferredLanguage: string;
  constraints: string[];
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
}

export interface LearningProgressEvent {
  stream_id?: string;
  event_type:
    | 'session_created'
    | 'stage_started'
    | 'artifact_saved'
    | 'stage_completed'
    | 'session_completed'
    | 'session_failed';
  stage: string;
  status: 'created' | 'started' | 'saved' | 'completed' | 'failed';
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
  runId: string | null;
  status: LearningWorkspaceStatus;
  currentStage: string;
  completedStages: string[];
  events: LearningProgressEvent[];
  plan: LearningContentPlan | null;
  qualityReport: LearningQualityReport | null;
  evidenceGraph: LearningEvidenceGraph | null;
  submittedInput: LearningWorkspaceInput | null;
  failureKind: LearningFailureKind | null;
}
