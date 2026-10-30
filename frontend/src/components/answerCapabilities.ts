import type { AgentAnswer } from '../types';

export type AnswerCapability =
  | 'watchlist' | 'trend' | 'prediction' | 'network' | 'risk' | 'financial' | 'sentiment'
  | 'compliance' | 'esg' | 'legal' | 'business' | 'sourcing' | 'identity'
  | 'profile' | 'comparison' | 'quality' | 'delivery' | 'report' | 'generic';

export interface AnswerCapabilityMeta {
  label: string;
}

const CAPABILITY_META: Record<AnswerCapability, AnswerCapabilityMeta> = {
  watchlist: { label: '监控清单' },
  trend: { label: '风险变化' },
  prediction: { label: '风险趋势预测' },
  network: { label: '供应链关系与传染风险' },
  risk: { label: '综合风险分析' },
  financial: { label: '财务分析' },
  sentiment: { label: '舆情分析' },
  compliance: { label: '合规筛查' },
  esg: { label: 'ESG 评估' },
  legal: { label: '司法风险' },
  business: { label: '经营与交易分析' },
  sourcing: { label: '寻源建议' },
  identity: { label: '主体核验' },
  profile: { label: '工商资料' },
  comparison: { label: '供应商对比' },
  quality: { label: '质量风险' },
  delivery: { label: '交付风险' },
  report: { label: '风险报告' },
  generic: { label: '本轮分析结果' },
};

const SUMMARY_ROUTES: Array<{ capability: AnswerCapability; phrases: string[] }> = [
  { capability: 'watchlist', phrases: ['监控清单', '监控对象'] },
  { capability: 'prediction', phrases: ['风险趋势预测', '风险预测', '未来 6-12 个月', '未来6-12个月', '预测风险'] },
  { capability: 'trend', phrases: ['风险变化', '风险趋势', '趋势分析'] },
  { capability: 'network', phrases: ['供应链关系', '关联关系', '传染风险', '风险传染'] },
  { capability: 'risk', phrases: ['综合风险分析', '综合风险评估'] },
  { capability: 'financial', phrases: ['财务分析', '财务数据', '财务指标'] },
  { capability: 'sentiment', phrases: ['舆情分析', '舆情新闻', '舆情'] },
  { capability: 'compliance', phrases: ['合规筛查', '合规分析', '制裁筛查', '黑名单'] },
  { capability: 'esg', phrases: ['ESG 评估', 'ESG分析', 'ESG 分析', '环境、社会和治理'] },
  { capability: 'legal', phrases: ['司法风险', '司法分析', '诉讼分析', '被执行', '失信'] },
  { capability: 'sourcing', phrases: ['寻源候选', '寻源建议', '替代供应商', '供应商候选'] },
  { capability: 'profile', phrases: ['工商资料', '企业基本资料', '注册地址', '法定代表人'] },
  { capability: 'identity', phrases: ['主体核验', '主体身份', '统一社会信用代码', '登记状态'] },
  { capability: 'comparison', phrases: ['供应商对比', '企业对比', '横向对比', '对比分析'] },
  { capability: 'quality', phrases: ['质量风险', '质量评估', '质量分析', '质量/交付查询', '质量/交付指标'] },
  { capability: 'delivery', phrases: ['交付风险', '交付评估', '交付分析', '履约分析'] },
  { capability: 'report', phrases: ['风险报告', '生成报告', '评估报告'] },
  { capability: 'business', phrases: ['经营风险', '经营分析', '商务风险', '商务分析', '交易分析'] },
];

const DIMENSION_ROUTES: Array<{ capability: AnswerCapability; dimensions: string[] }> = [
  { capability: 'watchlist', dimensions: ['risk_monitoring'] },
  { capability: 'prediction', dimensions: ['risk_prediction'] },
  { capability: 'network', dimensions: ['risk_network'] },
  { capability: 'financial', dimensions: ['financial'] },
  { capability: 'sentiment', dimensions: ['sentiment'] },
  { capability: 'compliance', dimensions: ['compliance'] },
  { capability: 'esg', dimensions: ['esg'] },
  { capability: 'sourcing', dimensions: ['sourcing'] },
  { capability: 'identity', dimensions: ['identity', 'identity_review'] },
  { capability: 'report', dimensions: ['report'] },
  { capability: 'quality', dimensions: ['quality'] },
  { capability: 'delivery', dimensions: ['delivery'] },
  { capability: 'business', dimensions: ['business', 'business_risk', 'operational'] },
];

const LEGAL_FACT_PATHS = ['lawsuit', 'court', 'executed', 'dishonesty', 'consumption_restriction', 'major_lawsuit', 'legal_person_change'];

function matchesAny(value: string, phrases: string[]): boolean {
  return phrases.some(phrase => value.includes(phrase));
}

export function getAnswerCapability(answer?: AgentAnswer | null): AnswerCapability {
  if (!answer) return 'generic';
  const summaryRoute = SUMMARY_ROUTES.find(route => matchesAny(answer.summary || '', route.phrases));
  if (summaryRoute) return summaryRoute.capability;
  const dimensions = answer.claims.map(claim => claim.dimension);
  const isLegacySpecializedSummary = matchesAny(answer.summary || '', ['供应商复核', '综合风险评分']);
  if (answer.summary.trim() && !isLegacySpecializedSummary) return 'generic';
  const dimensionRoute = DIMENSION_ROUTES.find(route => route.dimensions.some(dimension => dimensions.includes(dimension)));
  if (dimensionRoute) return dimensionRoute.capability;
  if (answer.claims.some(claim => matchesAny(claim.fact_path || '', LEGAL_FACT_PATHS))) return 'legal';
  if (answer.claims.some(claim => (claim.fact_path || '').startsWith('financial.'))) return 'financial';
  if (answer.claims.some(claim => (claim.fact_path || '').startsWith('risk_detail.'))) return 'legal';
  return 'generic';
}

export function getAnswerCapabilityMeta(capability: AnswerCapability): AnswerCapabilityMeta {
  return CAPABILITY_META[capability];
}

export function isNetworkAnswer(answer?: AgentAnswer | null): boolean {
  return getAnswerCapability(answer) === 'network';
}
