// risk_score is a safety score: higher values mean lower risk.
const HIGH = { min: 0, label: '高风险', color: '#e06060', bg: '#fef5f5' };
const MEDIUM = { min: 40, label: '中风险', color: '#d4a040', bg: '#fffbf0' };
const LOW = { min: 70, label: '低风险', color: '#2d8c63', bg: '#ecfdf5' };

export const RISK_LEVELS = { low: LOW, medium: MEDIUM, high: HIGH } as const;

export function getRiskColor(score: number): string {
  if (score >= LOW.min) return LOW.color;
  if (score >= MEDIUM.min) return MEDIUM.color;
  return HIGH.color;
}

export function getRiskBg(score: number): string {
  if (score >= LOW.min) return LOW.bg;
  if (score >= MEDIUM.min) return MEDIUM.bg;
  return HIGH.bg;
}

export function getRiskLevel(score: number): string {
  if (score >= LOW.min) return LOW.label;
  if (score >= MEDIUM.min) return MEDIUM.label;
  return HIGH.label;
}
