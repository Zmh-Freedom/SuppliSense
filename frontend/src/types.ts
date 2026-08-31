export interface CompanyProfile {
  company_name: string;
  legal_person: string;
  registered_capital: string;
  establish_time: string;
  is_listed: boolean;
}

export interface RiskDetail {
  lawsuit_count: number;
  recent_lawsuits?: number;
  executed_count: number;
  dishonesty_count: number;
  major_lawsuit: boolean;
  abnormal_operation_count: number;
  administrative_penalty_count: number;
  legal_person_change_frequent: boolean;
  guarantee_count?: number;
  pledge_count?: number;
  bankruptcy_count?: number;
  env_penalty_count?: number;
}

export interface FinancialMetrics {
  revenue_growth: number;
  net_profit_growth: number;
  debt_ratio: number;
  cash_flow: number;
  roe?: number;
  net_profit_margin?: number;
  current_ratio?: number;
  quick_ratio?: number;
  equity_ratio?: number;
  inventory_turnover?: number;
  ar_turnover_days?: number;
  recurring_profit_ratio?: number;
  revenue_trend?: number;
  net_profit_trend?: number;
  debt_trend?: number;
}

export interface RiskResult {
  risk_score: number;
  risk_level: string;
  financial: FinancialMetrics | null;
  risk_detail: RiskDetail;
  cached_at: string | null;
  cache_age_hours: number | null;
  is_stale: boolean;
  is_listed: boolean;
}

export interface AlertDoc {
  _id: string;
  company_name: string;
  created_at: string;
  changes: { field: string; old: number | boolean; new: number | boolean }[];
  severity: 'warning' | 'critical';
  read?: boolean;
}

export interface WatchlistData {
  count: number;
  companies: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  references?: SupplierReference[];
}

export interface SupplierReference {
  name: string;
  kind?: 'supplier' | string;
  source?: string;
  discovery_source?: string;
  website_url?: string;
  website_status?: 'unverified' | 'not_found' | string;
  website_url_source?: string;
  contact_phone?: string;
  contact_phone_source?: string;
  contact_email?: string;
  contact_email_source?: string;
  contact_status?: 'unverified' | 'not_found' | string;
}

// ---- Chat Charts ----
export interface ChartData {
  type: 'line' | 'bar' | 'radar' | 'pie' | 'gauge';
  title: string;
  data: Array<Record<string, unknown>>;
  source?: 'tool' | 'llm';
  tool?: string;
}

// ---- Dashboard ----
export interface Prediction {
  company_name: string;
  predicted_score: number;
  predicted_level: string;
  probability: 'high' | 'medium' | 'low';
  label: string;
  signals: { signal: string; weight: number }[];
  trend: 'rising' | 'falling' | 'stable';
}

// ---- Macro / PMI ----
export interface PMIData {
  date: string;
  value: number;
  label: string;
}

export interface PMIOverview {
  manufacturing_pmi: number;
  non_manufacturing_pmi: number;
  error?: boolean;
}

export interface AlternativeDashboard {
  total_alternatives: number;
  industries: string[];
  high_risk_count: number;
}

// ---- WebSocket Events ----
export interface AccessApplicationItem {
  application_id: string;
  supplier_name: string;
  request_id: string | null;
  applicant_id: string;
  status: string;
  reviewer_id: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface WSEventMap {
  alert: AlertDoc;
  alert_update: AlertDoc;
  risk_update: { company_name: string; score: number; level: string };
  task_complete: { task_id: string; status: string; result?: unknown };
  notification: { message: string; type: string };
  sentiment_ready: { company_name: string };
  risk_alert: { company: string; score_delta: number; changes: Array<{ field: string; old: unknown; new: unknown }> };
  sourcing_suggestion: {
    company: string;
    risk_score: number;
    level_escalated: boolean;
    alternatives: Array<{
      supplier_name: string;
      match_score: number;
      risk_score: number;
      risk_level: string;
    }>;
  };
}

// ---- Sourcing ----
export interface SourcingRequestInput {
  title: string;
  category: string;
  spec: string;
  budget_min?: number;
  budget_max?: number;
  quantity?: number;
  region_required?: string;
  qualifications?: string[];
}

export interface SourcingResultItem {
  result_id: string;
  supplier_id?: string | null;
  supplier_code?: string | null;
  supplier_name: string;
  match_score: number;
  risk_score: number | null;
  risk_level: string;
  final_rank: number;
  match_reason: string;
  risk_summary: string;
  industry?: string | null;
  categories?: string[];
  capabilities?: Array<Record<string, unknown>>;
  contacts?: Array<Record<string, unknown>>;
  website_url?: string | null;
  contact_person?: string | null;
  contact_phone?: string | null;
  contact_email?: string | null;
  source?: string | null;
  source_updated_at?: string | null;
  selected: boolean;
  action: string | null;
}

export interface SourcingSearchResponse {
  request_id: string;
  status: string;
  results: SourcingResultItem[];
}

export interface SourcingRequestDetail {
  request_id: string;
  user_id: string;
  title: string;
  category: string;
  spec: string;
  status: string;
  result_count: number;
  created_at: string;
  completed_at: string | null;
  results: SourcingResultItem[];
}

// ---- Sourcing Risk V2 agent runs ----
export type AgentRunStatus =
  | 'CREATED'
  | 'CLARIFYING'
  | 'POLICY_LOCKED'
  | 'LOCAL_SEARCHING'
  | 'EXTERNAL_REVIEW'
  | 'IDENTITY_RESOLVING'
  | 'IDENTITY_REVIEW'
  | 'INVESTIGATING'
  | 'EVIDENCE_REVIEW'
  | 'SCORING'
  | 'READY_FOR_REVIEW'
  | 'ACTION_PENDING'
  | 'ACTION_EXECUTING'
  | 'COMPLETED'
  | 'PARTIAL'
  | 'NEEDS_REVIEW'
  | 'ACTION_FAILED'
  | 'FAILED'
  | 'CANCELLED';

export interface SourcingRiskRequirement {
  requirement_text: string;
  category?: string | null;
  specification?: string | null;
  expected_candidate_count?: number;
  [key: string]: unknown;
}

export interface SourcingRiskIdentityCandidate {
  company_id: string;
  legal_name?: string;
  name?: string;
}

export interface SourcingRiskEvidence {
  evidence_id?: string;
  dimension?: string;
  freshness_status?: 'fresh' | 'stale' | 'unknown' | string;
  conflict_status?: 'clear' | 'conflicting' | 'unknown' | string;
  claim_code?: string;
  source?: string;
}

export interface SourcingRiskCandidate {
  id?: string;
  candidate_id?: string;
  company_id?: string | null;
  supplier_id?: string | null;
  supplier_name?: string;
  name?: string;
  supplier_code?: string | null;
  source?: 'local' | 'staged_external' | string;
  status?: string;
  industry?: string | null;
  categories?: string[];
  capabilities?: Array<Record<string, unknown>>;
  contacts?: Array<Record<string, unknown>>;
  source_updated_at?: string | null;
  website_url?: string;
  website_status?: 'unverified' | 'not_found' | string;
  website_url_source?: string;
  contact_phone?: string;
  contact_email?: string;
  contact_status?: 'unverified' | 'not_found' | string;
  contact_phone_source?: string;
  contact_email_source?: string;
  contact_enrichment_status?: 'partial' | 'not_found' | string;
  identity_status?: string;
  identity_review?: boolean;
  identity_candidates?: SourcingRiskIdentityCandidate[];
  evidence?: SourcingRiskEvidence[];
  evidence_by_dimension?: SourcingRiskEvidence[];
  [key: string]: unknown;
}

export interface SourcingRiskDecision {
  company_id?: string;
  candidate_id?: string;
  group: 'recommended' | 'alternative' | 'needs_review' | 'rejected' | string;
  final_score?: number | null;
  confidence?: number;
  reason_codes?: string[];
  evidence_ids?: string[];
}

export interface SourcingRiskApprovalProposal {
  id: string;
  action_type: string;
  status: string;
  payload: Record<string, unknown>;
  execution_state?: string;
}

export interface SourcingRiskApproval {
  id?: string;
  proposal_id: string;
  decision: 'approved' | 'rejected' | string;
  comment?: string | null;
}

export interface SourcingRiskRawPayloadStatus {
  raw_payload_ref: string;
  lifecycle_status: 'committed' | 'pending' | 'pending_compensation' | string;
}

export interface SourcingRiskAgentRun {
  id?: string;
  run_id?: string;
  status: AgentRunStatus | string;
  version: number;
  requirement: SourcingRiskRequirement;
  candidates?: SourcingRiskCandidate[];
  evidence_by_company_id?: Record<string, SourcingRiskEvidence[]>;
  evidence_reviews?: Record<string, { status?: string; reason_codes?: string[] }>;
  decisions?: SourcingRiskDecision[];
  proposals?: SourcingRiskApprovalProposal[];
  action_proposals?: SourcingRiskApprovalProposal[];
  approvals?: SourcingRiskApproval[];
  raw_payload_statuses?: SourcingRiskRawPayloadStatus[];
  next_action?: string | null;
  error_code?: string | null;
}

export interface AgentRunEvent {
  eventId: number;
  eventType: string;
  data: Record<string, unknown>;
}

export interface AgentTraceEvent {
  eventId: number;
  kind: string;
  status: string;
  message: string;
  data: Record<string, unknown>;
  source: 'agent_trace' | 'graph_trace';
  atMs?: number;
}

export interface SupplierEntry {
  _id: string;
  name: string;
  unified_code?: string;
  legal_person?: string;
  registered_capital?: string;
  establish_time?: string;
  reg_status?: string;
  industry?: string;
  categories: string[];
  regions: string[];
  products?: string[];
  capabilities?: Array<Record<string, unknown>>;
  status: string;
  source?: string;
}

// ---- AssessView Expandable ----
export interface JudicialDetail {
  count: number;
  detail: { case_number: string; amount?: number; date?: string; type?: string }[];
}

export interface PolicyRisk {
  score: number;
  count: number;
  tags: { tag: string; level: string; desc: string }[];
}

export interface Alternative {
  company_name: string;
  industry: string;
  risk_score: number | null;
  risk_level: string;
}

export interface RelatedEntity {
  name: string;
  relation: string;
  risk_score?: number;
}

export interface JudicialMatch {
  case_number: string;
  type: string;
  amount?: number;
  date?: string;
}

// ---- ESG ----
export interface ESGDimension {
  score: number;
  level: string;
  detail: { item: string; value: string | number }[];
}

export interface ESGResult {
  environmental: ESGDimension;
  social: ESGDimension;
  governance: ESGDimension;
}

// ---- Macro Risk ----
export interface MacroRiskResult {
  company_name: string;
  assessed_at: string;
  total_score: number;
  total_level: string;
  policy_risks: PolicyRisk;
  regional_risk: { province: string; score: number; level: string; label?: string };
  industry_risk: { industry: string; pmi_value?: number; pmi_label?: string; risk_score: number; risk_level: string; pmi_date?: string };
}

// ---- Alternatives ----
export interface AlternativeResult {
  company_name: string;
  source_industry: string;
  source_risk_score: number | null;
  alternatives_count: number;
  alternatives: Alternative[];
}

// ---- Contagion ----
export interface ContagionResult {
  company_name: string;
  related_count: number;
  branch_count: number;
  dependency_count: number;
  high_risk_related_count: number;
  related_entities: { name: string; relation_type: string; risk_score?: number }[];
}

// ---- Scenario ----
export interface ScenarioResult {
  scenario: string;
  scenario_desc: string;
  impact_score: number;
  impact_level: string;
  suggested_actions: string[];
}

// ---- Sanctions ----
export interface SanctionsMatch {
  name?: string;
  detail?: string;
  country?: string;
  program?: string;
  level?: string;
}

export interface SanctionsResult {
  clean: boolean;
  match_count: number;
  matches: SanctionsMatch[];
}

// ---- Supplier Master Data ----

export interface SupplierMasterData {
  _id: string;
  name: string;
  unified_code?: string;
  legal_person?: string;
  registered_capital?: string;
  establish_time?: string;
  reg_status?: string;
  categories: string[];
  regions: string[];
  contact_person?: string;
  contact_phone?: string;
  contact_email?: string;
  website_url?: string;
  address?: string;
  scale?: string;
  description?: string;
  certifications: { type: string; cert_number?: string; valid_until?: string }[];
  annual_revenue?: number;
  credit_rating?: string;
  status: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
}

// ---- Supplier Profile (aggregated) ----

export interface ProfileBasicInfo {
  name: string;
  unified_code?: string;
  legal_person?: string;
  registered_capital?: string;
  establish_time?: string;
  reg_status?: string;
  industry?: string;
  categories: string[];
  regions: string[];
  scale?: string;
  address?: string;
  contact_person?: string;
  contact_phone?: string;
  contact_email?: string;
  website_url?: string;
  source?: string;
  updated_at?: string;
  industry_source?: string;
  industry_updated_at?: string;
  website_url_source?: string;
  contact_phone_source?: string;
  contact_email_source?: string;
  status: string;
}

export interface ProfileRiskSummary {
  risk_score: number;
  risk_level: string;
  trend: { date: string; risk_score: number; risk_level?: string }[];
  alert_count: number;
  last_checked?: string;
  in_watchlist: boolean;
}

export interface ProfileFinancialSnapshot {
  revenue_growth?: number;
  net_profit_growth?: number;
  debt_ratio?: number;
  cash_flow?: number;
  roe?: number;
  net_profit_margin?: number;
  current_ratio?: number;
  quick_ratio?: number;
  credit_rating?: string;
  annual_revenue?: number;
  cached_at?: string;
  history: {
    period: string;
    revenue?: number;
    net_profit?: number;
    debt_ratio?: number;
    cash_flow?: number;
  }[];
}

export interface ProfileSentimentSummary {
  overall_sentiment: string;
  sentiment_score: number;
  negative_ratio: number;
  article_count: number;
  top_tags: string[];
  analyzed_at?: string;
}

export interface ProfileComplianceStatus {
  sanctions_clean: boolean;
  sanctions_match_count: number;
  lawsuit_count: number;
  executed_count: number;
  dishonesty_count: number;
  abnormal_operation_count: number;
  administrative_penalty_count: number;
  tax_arrears_count: number;
}

export interface ProfileESGSummary {
  environmental?: { score: number; level: string; detail: { item: string; value: string | number }[] };
  social?: { score: number; level: string; detail: { item: string; value: string | number }[] };
  governance?: { score: number; level: string; detail: { item: string; value: string | number }[] };
}

export interface ProfileAlertItem {
  _id: string;
  company_name?: string;
  severity: string;
  changes: { field: string; old: unknown; new: unknown }[];
  created_at: string;
}

export interface ProfileRelationshipSummary {
  related_count: number;
  branch_count: number;
  dependency_count: number;
  high_risk_related_count: number;
  entities: { name: string; relation_type: string; risk_score?: number; supplier_id?: string }[];
}

export interface ChangelogEntry {
  changed: Record<string, { old: unknown; new: unknown }>;
  changed_at: string;
}

export interface SupplierProfile {
  basic_info: ProfileBasicInfo;
  risk?: ProfileRiskSummary;
  financial?: ProfileFinancialSnapshot;
  sentiment?: ProfileSentimentSummary;
  compliance?: ProfileComplianceStatus;
  esg?: ProfileESGSummary;
  alerts: ProfileAlertItem[];
  relationships?: ProfileRelationshipSummary;
  changelog: ChangelogEntry[];
}

export interface BusinessRiskP0 {
  assessment_status: 'partial' | 'missing_data' | 'needs_scope' | 'missing_supplier' | string;
  assessment_data_mode?: 'formal' | 'demo';
  decision_usable?: boolean;
  period?: string;
  coverage?: number;
  formal_business_score?: number | null;
  reason?: string;
  available_category_codes?: string[];
  enabled_dimension?: {
    name: string;
    model_weight: number;
    risk_level: 'low' | 'medium' | 'high' | string;
    supplier_spend_share: number;
    supplier_received_amount: number;
    category_total_received_amount: number;
    active_supplier_count: number;
    single_source: boolean;
  };
  observed_signals?: {
    contract?: { status: string; active_rows?: number; expiring_rows?: number; expired_rows?: number; unsigned_rows?: number };
    settlement?: { status: string; unsettled_amount?: number | null; unsettled_ratio?: number | null };
    price?: { status: string; current_weighted_price?: number | null; change_ratio?: number | null };
  };
  limitations?: string[];
  evidence?: { source: string; period?: string; claim: string; data_mode?: string; rows?: number }[];
}
