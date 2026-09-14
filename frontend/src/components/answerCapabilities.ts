import type { AgentAnswer } from '../types';

export function isNetworkAnswer(answer?: AgentAnswer | null): boolean {
  if (!answer) return false;
  return answer.summary.includes('供应链关系')
    || answer.summary.includes('传染风险')
    || answer.claims.some(claim => claim.dimension === 'risk_network' || claim.fact_path?.startsWith('risk_network.'));
}
