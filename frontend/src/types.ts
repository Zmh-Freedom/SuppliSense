export interface CompanyProfile {
  company_name: string;
  legal_person: string;
  registered_capital: string;
  establish_time: string;
  is_listed: boolean;
}

export interface RiskDetail {
  lawsuit_count: number;
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
}

export interface RiskResult {
  risk_score: number;
  risk_level: string;
  financial: FinancialMetrics | null;
  risk_detail: RiskDetail;
}

export interface AlertDoc {
  company_name: string;
  created_at: string;
  changes: { field: string; old: number | boolean; new: number | boolean }[];
  severity: 'warning' | 'critical';
}

export interface WatchlistData {
  count: number;
  companies: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
