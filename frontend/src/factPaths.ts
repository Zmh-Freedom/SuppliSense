const FACT_PATH_ALIASES: Record<string, string> = {
  'revenue_growth': 'revenue_growth',
  'net_profit_growth': 'net_profit_growth',
  'debt_ratio': 'debt_ratio',
  'cash_flow': 'cash_flow',
  'net_profit_margin': 'net_profit_margin',
  'current_ratio': 'current_ratio',
  'quick_ratio': 'quick_ratio',
  'return_on_equity': 'roe',
  'roe': 'roe',
  'revenue': 'revenue',
  'net_profit': 'net_profit',
};

export function canonicalFactPath(path: string, extraKnownPaths: string[] = []): string {
  const normalized = path
    .trim()
    .replace(/([a-z])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
    .replace(/\.{2,}/g, '.')
    .replace(/^\.+|\.+$/g, '');
  if (FACT_PATH_ALIASES[normalized]) return FACT_PATH_ALIASES[normalized];
  const knownPaths = ['risk_score', 'risk_level', 'quality_rate', 'on_time_rate', ...Object.values(FACT_PATH_ALIASES), ...extraKnownPaths];
  const compact = normalized.replace(/[._]/g, '');
  const matched = knownPaths.find(item => item.replace(/[._]/g, '').toLowerCase() === compact);
  return matched || normalized;
}

