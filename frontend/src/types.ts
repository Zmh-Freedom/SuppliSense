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
  supplier_name: string;
  match_score: number;
  risk_score: number | null;
  risk_level: string;
  final_rank: number;
  match_reason: string;
  risk_summary: string;
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

export interface SupplierEntry {
  _id: string;
  name: string;
  unified_code?: string;
  legal_person?: string;
  registered_capital?: string;
  establish_time?: string;
  reg_status?: string;
  categories: string[];
  regions: string[];
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
  severity: string;
  changes: { field: string; old: unknown; new: unknown }[];
  created_at: string;
}

export interface ProfileRelationshipSummary {
  related_count: number;
  branch_count: number;
  dependency_count: number;
  high_risk_related_count: number;
  entities: { name: string; relation_type: string; risk_score?: number }[];
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
