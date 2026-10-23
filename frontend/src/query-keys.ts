export const queryKeys = {
  watchlist: ['alert', 'watchlist'] as const,
  dashboard: ['alert', 'dashboard'] as const,
  alertHistory: ['alert', 'history'] as const,
  predictions: ['alert', 'predict'] as const,
  alertTrend: (days: number) => ['trend', 'alert', days] as const,
  riskTrend: (name: string) => ['trend', 'risk', name] as const,
  sentimentDashboard: ['sentiment', 'dashboard'] as const,
  sentimentDetail: (name: string) => ['sentiment', 'detail', name] as const,
  esgList: ['p2', 'esg'] as const,
  esgDetail: (name: string) => ['p2', 'esg', name] as const,
  pmi: ['analysis', 'macro', 'pmi'] as const,
  alternativeDashboard: ['analysis', 'alternatives'] as const,
  macroDetail: (name: string) => ['analysis', 'macro', name] as const,
  alternativeDetail: (name: string) => ['analysis', 'alternatives', name] as const,
  scenarioDetail: (name: string, scenario: string) =>
    ['analysis', 'scenario', name, scenario] as const,
  sanctionsDetail: (name: string) => ['analysis', 'sanctions', name] as const,
  notifications: (limit?: number, read?: boolean) =>
    ['notifications', { limit, read }] as const,
  unreadCount: ['notifications', 'unread'] as const,
  sourcingRequests: ['sourcing', 'requests'] as const,
  sourcingDetail: (id: string) => ['sourcing', 'requests', id] as const,
  agentRunDetail: (id: string) => ['agent-runs', id] as const,
  suppliers: ['sourcing', 'suppliers'] as const,
  accessApplications: (status?: string) => ['sourcing', 'access-applications', status] as const,
  supplierProfile: (id: string) => ['supplier', 'profile', id] as const,
  supplierComplianceStatus: (id: string) => ['supplier', 'compliance-status', id] as const,
  supplierBusinessRisk: (id: string) => ['supplier', 'business-risk', id] as const,
  supplierRiskSnapshots: (name: string, limit?: number) => ['supplier', 'risk-snapshots', name, limit] as const,
  supplierChangelog: (id: string, limit?: number) => ['supplier', 'changelog', id, limit] as const,
  monitorRiskDetails: ['monitor-risk-detail'] as const,
  monitorRiskDetail: (id: string | undefined) => ['monitor-risk-detail', id] as const,
  monitorReviewTasks: ['monitor-review-task'] as const,
  monitorReviewTask: (id: string | undefined) => ['monitor-review-task', id] as const,
  monitorIdentityCandidates: ['monitor-identity-candidates'] as const,
  monitorIdentityCandidate: (id: string | undefined) => ['monitor-identity-candidates', id] as const,
  procurementCases: (filters: Record<string, string>) => ['procurement-cases', filters] as const,
  procurementCase: (id: string | undefined) => ['procurement-case', id] as const,
} as const;

// Prefix keys include inactive variants, so reconnect also repairs pages
// mounted later. API authorization is applied again on every refetch.
const MONITOR_KEYS = [queryKeys.monitorRiskDetails, queryKeys.monitorReviewTasks, queryKeys.monitorIdentityCandidates, ['procurement-cases']] as const;
const ALERT_KEYS = [queryKeys.watchlist, queryKeys.dashboard, queryKeys.alertHistory, queryKeys.predictions, ['trend'], ['notifications'], ...MONITOR_KEYS] as const;
const RESULT_KEYS = [queryKeys.watchlist, queryKeys.dashboard, queryKeys.predictions, ['supplier'], ['trend'], ...MONITOR_KEYS] as const;
export const REALTIME_QUERY_KEYS: Record<string, readonly (readonly unknown[])[]> = {
  alert_update: ALERT_KEYS,
  risk_alert: ALERT_KEYS,
  risk_update: RESULT_KEYS,
  assessment_updated: RESULT_KEYS,
  sentiment_ready: [['sentiment']],
  company_updated: [queryKeys.suppliers, queryKeys.watchlist, ['supplier'], ['analysis'], ...MONITOR_KEYS],
  agent_run_updated: [['agent-runs']],
  action_updated: [['agent-runs'], ['sourcing'], ['supplier'], ...ALERT_KEYS],
  task_complete: [['agent-runs'], ['sourcing'], ['supplier'], ...RESULT_KEYS],
  notification: [['notifications']],
  sourcing_suggestion: [['notifications'], queryKeys.alternativeDashboard],
  proactive_analysis: [['notifications'], queryKeys.dashboard],
  realtime_resync: [['alert'], ['trend'], ['sentiment'], ['p2'], ['analysis'], ['notifications'], ['sourcing'], ['agent-runs'], ['supplier'], ...MONITOR_KEYS],
};
