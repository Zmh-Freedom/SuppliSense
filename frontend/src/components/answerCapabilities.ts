import type { AgentAnswer } from '../types';

export type AnswerCapability =
  | 'watchlist' | 'trend' | 'prediction' | 'network' | 'risk' | 'financial' | 'sentiment'
  | 'compliance' | 'esg' | 'legal' | 'business' | 'sourcing' | 'identity'
  | 'profile' | 'comparison' | 'quality' | 'delivery' | 'report' | 'generic';

export interface AnswerCapabilityMeta {
  label: string;
  nextAction: string;
  suggestionTitle: string;
  suggestion: string;
}

const CAPABILITY_META: Record<AnswerCapability, AnswerCapabilityMeta> = {
  watchlist: { label: '监控清单', nextAction: '选择供应商查看详情', suggestionTitle: '监控清单建议', suggestion: '可选择供应商查看当前风险快照；如需加入或调整监控，请继续确认监控对象和数据范围。' },
  trend: { label: '风险变化', nextAction: '查看每家供应商的变化', suggestionTitle: '趋势分析建议', suggestion: '优先查看风险恶化的供应商，并结合最近一期证据确认变化是否需要升级采购跟进。' },
  prediction: { label: '风险趋势预测', nextAction: '查看预测信号与数据边界', suggestionTitle: '预测使用建议', suggestion: '预测结果用于安排重点关注，不替代当前风险核验；请结合预测信号和最新证据制定跟进动作。' },
  network: { label: '供应链关系与传染风险', nextAction: '查看关联实体和关系图', suggestionTitle: '关系分析建议', suggestion: '建议结合关联实体、分支机构、供应链依赖和同行业关联查看关系图谱；如发现高风险关联主体，再安排定向供应商复核。' },
  risk: { label: '综合风险分析', nextAction: '查看风险明细与证据', suggestionTitle: '风险分析建议', suggestion: '结合综合安全评分、风险信号和证据覆盖范围判断供应商状态；评分不替代对重大诉讼、财务变化等具体信号的复核。' },
  financial: { label: '财务分析', nextAction: '查看财务指标与报告期', suggestionTitle: '财务分析建议', suggestion: '结合报告期、盈利变化、现金流和偿债指标判断供应商的履约承受能力；关键订单应补充核对最新财报或财务说明。' },
  sentiment: { label: '舆情分析', nextAction: '查看舆情新闻与来源', suggestionTitle: '舆情分析建议', suggestion: '查看每条新闻的摘要、来源和发布时间，优先核实负面或异常信息是否与供应商主体及当前合作有关。' },
  compliance: { label: '合规筛查', nextAction: '查看命中记录与适用范围', suggestionTitle: '合规筛查建议', suggestion: '结合命中记录、主体范围和数据更新时间进行判断；未命中不代表无需持续筛查，重大采购应保留核验记录。' },
  esg: { label: 'ESG 评估', nextAction: '查看 ESG 指标与等级', suggestionTitle: 'ESG 分析建议', suggestion: '结合环境、社会和治理维度的具体指标理解等级，不要只依据综合分数判断供应商是否适合当前采购场景。' },
  legal: { label: '司法风险', nextAction: '查看案件状态与影响', suggestionTitle: '司法分析建议', suggestion: '查看案件类型、状态、涉诉金额和时间，判断司法记录是否会影响持续经营、交付或合同履约。' },
  business: { label: '经营与交易分析', nextAction: '查看经营与交易信号', suggestionTitle: '经营分析建议', suggestion: '结合采购依赖、结算和收货变化核对业务背景；交易波动是复核信号，不应单独推断供应中断或经营异常。' },
  sourcing: { label: '寻源建议', nextAction: '查看候选供应商与匹配依据', suggestionTitle: '寻源建议', suggestion: '查看候选供应商的匹配条件、供货能力、来源和风险摘要，再按主体核验与报价流程推进。' },
  identity: { label: '主体核验', nextAction: '确认正式供应商主体', suggestionTitle: '主体核验建议', suggestion: '先确认企业全称、统一社会信用代码和登记状态，再继续风险分析或加入监控。' },
  profile: { label: '工商资料', nextAction: '查看企业基本资料', suggestionTitle: '企业资料建议', suggestion: '核对登记状态、法定代表人、注册地址和股东等主体资料，作为后续风险判断的身份基础。' },
  comparison: { label: '供应商对比', nextAction: '查看各供应商对比项', suggestionTitle: '对比分析建议', suggestion: '先确认各供应商的数据口径和更新时间，再结合采购品类、交付要求与风险偏好做选择。' },
  quality: { label: '质量风险', nextAction: '查看质量检查项', suggestionTitle: '质量分析建议', suggestion: '结合质量记录、整改状态和产品范围核验对当前采购的影响，必要时要求供应商提供质量证明。' },
  delivery: { label: '交付风险', nextAction: '查看交付与履约信号', suggestionTitle: '交付分析建议', suggestion: '结合交付记录、缺失月份和订单背景确认履约稳定性，不要仅凭单一交易波动做结论。' },
  report: { label: '风险报告', nextAction: '查看或下载报告', suggestionTitle: '报告使用建议', suggestion: '报告用于留存当前分析结果和证据边界；正式采购前仍应确认数据更新时间及待核实事项。' },
  generic: { label: '本轮分析结果', nextAction: '查看结论明细', suggestionTitle: '采购建议', suggestion: '当前已取得的数据未见需要立即暂停采购的信号，可按正常流程推进并持续关注指标变化。' },
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
  { capability: 'identity', phrases: ['主体核验', '主体身份', '统一社会信用代码', '登记状态'] },
  { capability: 'profile', phrases: ['工商资料', '企业基本资料', '注册地址', '法定代表人'] },
  { capability: 'comparison', phrases: ['供应商对比', '企业对比', '横向对比', '对比分析'] },
  { capability: 'quality', phrases: ['质量风险', '质量评估', '质量分析'] },
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
