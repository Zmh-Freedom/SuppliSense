export type ThemeName = 'light' | 'glass';

export const THEMES: { name: ThemeName; label: string }[] = [
  { name: 'light', label: '简约' },
  { name: 'glass', label: '毛玻璃' },
];

export const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626',
  '中风险': '#d97706',
  '低风险': '#16a34a',
  '未知': '#999',
};

export const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2',
  '中风险': '#fffbf0',
  '低风险': '#ecfdf5',
  '未知': '#f5f5f5',
};

export const SENTIMENT_COLORS: Record<string, string> = {
  negative: '#dc2626',
  neutral: '#6b7280',
  positive: '#16a34a',
};

export const SENTIMENT_LABEL: Record<string, string> = {
  negative: '负面',
  neutral: '中性',
  positive: '正面',
};
