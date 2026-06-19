const LOW = { max: 30, label: '低风险', color: '#2d8c63', bg: '#ecfdf5' };
const MEDIUM = { max: 60, label: '中风险', color: '#d4a040', bg: '#fffbf0' };
const HIGH = { max: 100, label: '高风险', color: '#e06060', bg: '#fef5f5' };

export const RISK_LEVELS = { low: LOW, medium: MEDIUM, high: HIGH } as const;

export function getRiskColor(score: number): string {
  if (score <= LOW.max) return LOW.color;
  if (score <= MEDIUM.max) return MEDIUM.color;
  return HIGH.color;
}

export function getRiskBg(score: number): string {
  if (score <= LOW.max) return LOW.bg;
  if (score <= MEDIUM.max) return MEDIUM.bg;
  return HIGH.bg;
}

export function getRiskLevel(score: number): string {
  if (score <= LOW.max) return LOW.label;
  if (score <= MEDIUM.max) return MEDIUM.label;
  return HIGH.label;
}

