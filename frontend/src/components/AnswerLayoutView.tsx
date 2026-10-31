import type { ReactNode } from 'react'
import type { AgentAnswer } from '../types'
import type { AnswerLayout } from './answerLayout'

const PROFILE_GROUP_LABELS: Record<string, string> = {
  baseinfo: '企业基本信息',
  holder: '股东信息',
  invest: '对外投资',
  change: '历史变更',
  branch: '分支机构',
  other: '其他已核验信息',
}

const DIMENSION_LABELS: Record<string, string> = {
  risk: '综合风险',
  financial: '财务数据',
  business_risk: '商务风险',
  quality: '质量数据',
  delivery: '交付数据',
  esg: 'ESG 数据',
  sentiment: '舆情信息',
  compliance: '合规筛查',
  legal_risk: '司法风险',
  operational_risk: '经营风险',
  risk_trend: '风险变化',
  risk_prediction: '风险趋势预测',
  risk_network: '供应链关系',
  identity_review: '主体核验',
  sourcing: '供应商候选',
  risk_comparison: '企业对比',
  risk_monitoring: '监控状态',
  company_profile: '工商资料',
}

const PROFILE_FIELD_LABELS: Record<string, string> = {
  'profile.baseinfo.name': '企业名称',
  'profile.baseinfo.creditCode': '统一社会信用代码',
  'profile.baseinfo.credit_code': '统一社会信用代码',
  'profile.baseinfo.legalPersonName': '法定代表人',
  'profile.baseinfo.legal_person_name': '法定代表人',
  'profile.baseinfo.regStatus': '登记状态',
  'profile.baseinfo.reg_status': '登记状态',
  'profile.baseinfo.regCapital': '注册资本',
  'profile.baseinfo.reg_capital': '注册资本',
  'profile.baseinfo.regLocation': '注册地址',
  'profile.baseinfo.reg_location': '注册地址',
  'profile.baseinfo.established_date': '成立日期',
  'profile.baseinfo.industry': '所属行业',
}

const PROFILE_STATUS_LABELS: Record<string, string> = {
  no_records: '明确无记录',
  query_failed: '查询失败',
  not_queried: '未查询',
  not_covered: '当前未覆盖',
}

const PROFILE_DOMAIN_LABELS: Record<string, string> = {
  baseinfo: '企业基本信息',
  holder: '股东',
  invest: '对外投资',
  change: '历史变更',
  branch: '分支机构',
}

const PROFILE_COUNT_PATHS: Record<string, string> = {
  baseinfo: 'counts.baseinfo',
  holder: 'counts.holder',
  invest: 'counts.invest',
  change: 'counts.changeInfo',
  branch: 'counts.branch',
}

function claimForIndex(answer: AgentAnswer, index: number) {
  return answer.claims[index]
}

function profileSubject(statement: string, label: string): string | null {
  const marker = `${label}：`
  const position = statement.indexOf(marker)
  if (position < 1) return null
  return statement.slice(0, position).replace(/工商资料[：，,]?/g, '').trim() || null
}

function displayValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  return typeof value === 'string' ? value : ''
}

function isZeroCount(value: unknown): boolean {
  return value === 0 || (typeof value === 'string' && value.trim() === '0')
}

function confirmedEmptyCount(answer: AgentAnswer, domain: string): string | null {
  const factPath = PROFILE_COUNT_PATHS[domain]
  if (!factPath) return null
  const counts = answer.claims.filter(claim => claim.validation_status === 'supported'
    && claim.dimension === 'company_profile'
    && claim.fact_path === factPath)
  if (counts.length !== 1 || !isZeroCount(counts[0].value)) return null
  return displayValue(counts[0].value)
}

function normalizeBoundaryText(text: string): string {
  return text.replace(/[\s：:，,。；;！？!、（）()]/g, '')
}

function limitationCoversStatus(limitation: string, domain: string, status: string): boolean {
  const domainLabel = PROFILE_DOMAIN_LABELS[domain]
  const statusLabel = PROFILE_STATUS_LABELS[status]
  if (!domainLabel || !statusLabel) return false
  return normalizeBoundaryText(limitation).includes(normalizeBoundaryText(`${domainLabel}${statusLabel}`))
}

function CompactFieldGroup({ group, indexes, answer }: { group: string; indexes: number[]; answer: AgentAnswer }) {
  const claims = indexes.map(index => claimForIndex(answer, index))
  const subject = claims.map(claim => {
    const label = PROFILE_FIELD_LABELS[claim.fact_path || '']
    return label ? profileSubject(claim.statement, label) : null
  }).find(Boolean)
  return <section role="group" aria-label={PROFILE_GROUP_LABELS[group] || '已核验字段'} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3.5">
    <h3 className="text-sm font-semibold text-[var(--color-text)]">{PROFILE_GROUP_LABELS[group] || '已核验字段'}</h3>
    {subject && <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{subject}</p>}
    <dl className="mt-3 grid gap-2 sm:grid-cols-2">
      {claims.map((claim, index) => <div key={`${claim.claim_id}-${index}`} className="min-w-0 rounded-lg bg-[var(--color-code-bg)]/55 px-3 py-2">
        <dt className="text-[11px] text-[var(--color-text-secondary)]">{PROFILE_FIELD_LABELS[claim.fact_path || '']}</dt>
        <dd className="mt-0.5 break-words text-sm font-medium text-[var(--color-text)]">{displayValue(claim.value)}</dd>
      </div>)}
    </dl>
  </section>
}

function FactList({ group, indexes, answer, expandable, confirmedEmptyDomains }: {
  group: string;
  indexes: number[];
  answer: AgentAnswer;
  expandable: boolean;
  confirmedEmptyDomains: Set<string>;
}) {
  const claims = indexes.map(index => claimForIndex(answer, index))
  const countPath = PROFILE_COUNT_PATHS[group]
  const visibleClaims = countPath && confirmedEmptyDomains.has(group)
    ? claims.filter(claim => !(claim.fact_path === countPath && isZeroCount(claim.value)))
    : claims
  if (visibleClaims.length === 0) return null
  const isProfileHolder = group === 'holder' && visibleClaims.every(claim => claim.dimension === 'company_profile')
  const holderCounts = isProfileHolder ? visibleClaims.filter(claim => claim.fact_path === 'counts.holder') : []
  const listClaims = isProfileHolder ? visibleClaims.filter(claim => claim.fact_path === 'profile.holder_names') : visibleClaims
  const subject = isProfileHolder
    ? listClaims.map(claim => profileSubject(claim.statement, '股东')).find(Boolean)
    : null
  const title = isProfileHolder ? '股东信息' : PROFILE_GROUP_LABELS[group] || DIMENSION_LABELS[group] || '已核验信息'
  const visible = expandable ? listClaims.slice(0, 5) : listClaims
  const remaining = expandable ? listClaims.slice(5) : []
  return <section role="group" aria-label={title} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3.5">
    <h3 className="text-sm font-semibold text-[var(--color-text)]">{title}{isProfileHolder && listClaims.length > 0 ? `（${listClaims.length}）` : ''}</h3>
    {subject && <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{subject}</p>}
    {holderCounts.map((claim, index) => <p key={`holder-count-${index}`} className="mt-2 text-xs text-[var(--color-text-secondary)]">股东信息记录数：{displayValue(claim.value)}</p>)}
    <ul className="mt-2 space-y-1.5 text-sm leading-6 text-[var(--color-text)]">
      {visible.map((claim, index) => <li key={`${claim.claim_id}-${index}`} className="break-words">{isProfileHolder ? displayValue(claim.value) : claim.statement}</li>)}
    </ul>
    {remaining.length > 0 && <details className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-code-bg)]/45 px-3 py-2">
      <summary className="min-h-9 cursor-pointer py-1 text-xs font-medium text-[var(--color-primary-bg)]">{isProfileHolder ? `查看其余 ${remaining.length} 位股东` : `展开其余 ${remaining.length} 项`}</summary>
      <ul className="space-y-1.5 pb-2 text-sm leading-6 text-[var(--color-text)]">
        {remaining.map((claim, index) => <li key={`${claim.claim_id}-${index + 5}`} className="break-words">{isProfileHolder ? displayValue(claim.value) : claim.statement}</li>)}
      </ul>
    </details>}
  </section>
}

function StatusAndLimits({ block, answer }: { block: AnswerLayout['b'][number]; answer: AgentAnswer }) {
  const limitations = (block.l || []).map(index => answer.limitations[index]).filter(Boolean)
  const uncertainties = block.u || []
  const statuses = (block.s || []).filter(([domain, status]) => (
    !limitations.some(limitation => limitationCoversStatus(limitation, domain, status))
  ))
  if (statuses.length === 0 && limitations.length === 0 && uncertainties.length === 0) return null
  return <section role="group" aria-label="资料状态与限制" className="rounded-xl border border-amber-200 bg-amber-50/60 p-3.5">
    <h3 className="text-sm font-semibold text-amber-950">资料状态与限制</h3>
    {statuses.length > 0 && <ul className="mt-2 flex flex-wrap gap-2">
      {statuses.map(([domain, status]) => {
        const count = status === 'no_records' ? confirmedEmptyCount(answer, domain) : null
        const countText = count === null ? '' : `（${count} 条记录）`
        return <li key={`${domain}-${status}`} className="rounded-full border border-amber-200 bg-white px-2.5 py-1 text-xs text-amber-900">{PROFILE_DOMAIN_LABELS[domain]}：{PROFILE_STATUS_LABELS[status]}{countText}</li>
      })}
    </ul>}
    {(limitations.length > 0 || uncertainties.length > 0) && <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-amber-950">
      {[...limitations, ...uncertainties].map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
    </ul>}
  </section>
}

export default function AnswerLayoutView({
  layout,
  answer,
  fallbackMarkdown,
  renderOverview,
}: {
  layout: AnswerLayout;
  answer: AgentAnswer;
  fallbackMarkdown: string;
  renderOverview: (content: string) => ReactNode;
}) {
  const confirmedEmptyDomains = new Set(layout.b.flatMap(block => (
    block.t === 'status_list' ? (block.s || []).filter(([, status]) => status === 'no_records').map(([domain]) => domain) : []
  )).filter(domain => confirmedEmptyCount(answer, domain) !== null))
  return <div className="space-y-3">
    {layout.b.map((block, index) => {
      if (block.t === 'overview') return <div key={`${block.g}-${index}`}>{renderOverview(fallbackMarkdown)}</div>
      if (block.t === 'field_group') return <CompactFieldGroup key={`${block.g}-${index}`} group={block.g} indexes={block.c || []} answer={answer} />
      if (block.t === 'expandable_list') return <FactList key={`${block.g}-${index}`} group={block.g} indexes={block.c || []} answer={answer} expandable confirmedEmptyDomains={confirmedEmptyDomains} />
      if (block.t === 'fact_list') return <FactList key={`${block.g}-${index}`} group={block.g} indexes={block.c || []} answer={answer} expandable={false} confirmedEmptyDomains={confirmedEmptyDomains} />
      if (block.t === 'status_list') return <StatusAndLimits key={`${block.g}-${index}`} block={block} answer={answer} />
      if (block.t === 'limitation_list') {
        const items = (block.l || []).map(limitIndex => answer.limitations[limitIndex]).filter(Boolean)
        return <section key={`${block.g}-${index}`} role="group" aria-label="数据覆盖与限制" className="rounded-xl border border-amber-200 bg-amber-50/60 p-3.5">
          <h3 className="text-sm font-semibold text-amber-950">数据覆盖与限制</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-amber-950">{items.map((item, itemIndex) => <li key={`${itemIndex}-${item}`}>{item}</li>)}</ul>
        </section>
      }
      return null
    })}
  </div>
}
