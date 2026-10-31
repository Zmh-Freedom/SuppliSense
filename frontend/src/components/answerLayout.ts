import type { AgentAnswer, AgentEvidenceRecord } from '../types'

export type AnswerLayoutBlock = {
  t: 'overview' | 'field_group' | 'expandable_list' | 'fact_list' | 'status_list' | 'limitation_list';
  g: string;
  c?: number[];
  s?: [string, string][];
  l?: number[];
  u?: string[];
}

export interface AnswerLayout {
  v: 1;
  b: AnswerLayoutBlock[];
}

export interface ParsedAnswerLayout {
  layout: AnswerLayout;
  fallbackMarkdown: string;
}

const MARKER_PATTERN = /^\[\/\/\]: # "ss-answer-layout:v1:([A-Za-z0-9_-]+)"\s*$/m
const PROFILE_FIELD_PATHS = new Set([
  'profile.baseinfo.name',
  'profile.baseinfo.creditCode', 'profile.baseinfo.credit_code',
  'profile.baseinfo.legalPersonName', 'profile.baseinfo.legal_person_name',
  'profile.baseinfo.regStatus', 'profile.baseinfo.reg_status',
  'profile.baseinfo.regCapital', 'profile.baseinfo.reg_capital',
  'profile.baseinfo.regLocation', 'profile.baseinfo.reg_location',
  'profile.baseinfo.established_date',
  'profile.baseinfo.industry',
])
const PROFILE_DOMAIN_LABELS: Record<string, string> = {
  baseinfo: '企业基本信息',
  holder: '股东',
  invest: '对外投资',
  change: '历史变更',
  branch: '分支机构',
}
const PROFILE_STATUS_LABELS: Record<string, string> = {
  no_records: '明确无记录',
  query_failed: '查询失败',
  not_queried: '未查询',
  not_covered: '当前未覆盖',
}
const LAYOUT_TYPES = new Set(['overview', 'field_group', 'expandable_list', 'fact_list', 'status_list', 'limitation_list'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every(key => allowed.includes(key))
}

function decodePayload(value: string): unknown {
  const normalized = value.replaceAll('-', '+').replaceAll('_', '/')
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4)
  const binary = atob(padded)
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0))
  return JSON.parse(new TextDecoder().decode(bytes)) as unknown
}

function claimIsEvidenceBound(
  index: number,
  answer: AgentAnswer,
  evidence: AgentEvidenceRecord[],
): boolean {
  if (!Number.isInteger(index) || index < 0 || index >= answer.claims.length) return false
  const claim = answer.claims[index]
  if (claim.validation_status !== 'supported' || claim.evidence_refs.length === 0) return false
  const currentAnswerRefs = new Set(answer.evidence_refs)
  return claim.evidence_refs.some(reference => currentAnswerRefs.has(reference)
    && evidence.some(record => {
      if (record.evidence_id !== reference
        || record.entity_id !== claim.entity_id
        || record.dimension !== claim.dimension
        || record.data_mode !== 'formal'
        || !['available', 'confirmed_empty'].includes(record.status)) return false
      if (!record.valid_until) return true
      const validUntil = Date.parse(record.valid_until)
      return Number.isFinite(validUntil) && validUntil >= Date.now()
    }))
}

function normalizedStatusText(value: string): string {
  return value.replace(/[\s：:，,。；;！？!、]/g, '')
}

function validIndexList(value: unknown, max: number): value is number[] {
  return Array.isArray(value)
    && value.length <= max
    && value.every(item => Number.isInteger(item) && item >= 0)
}

function validateBlock(
  value: unknown,
  answer: AgentAnswer,
  evidence: AgentEvidenceRecord[],
  fallbackMarkdown: string,
  usedClaims: Set<number>,
): value is AnswerLayoutBlock {
  if (!isRecord(value) || !hasOnlyKeys(value, ['t', 'g', 'c', 's', 'l', 'u'])) return false
  if (typeof value.t !== 'string' || !LAYOUT_TYPES.has(value.t) || typeof value.g !== 'string') return false
  const type = value.t
  const group = value.g
  const claimIndexes = value.c
  const limitationIndexes = value.l

  if (claimIndexes !== undefined) {
    if (!validIndexList(claimIndexes, 1000)) return false
    for (const index of claimIndexes) {
      if (usedClaims.has(index) || !claimIsEvidenceBound(index, answer, evidence)) return false
      const claim = answer.claims[index]
      if (type === 'field_group') {
        if (group !== 'baseinfo' || claim.dimension !== 'company_profile' || !claim.fact_path || !PROFILE_FIELD_PATHS.has(claim.fact_path) || claim.value == null) return false
      } else if (type === 'expandable_list' && group === 'holder') {
        if (claim.dimension !== 'company_profile'
          || !['profile.holder_names', 'counts.holder'].includes(claim.fact_path || '')
          || (claim.fact_path === 'counts.holder' && claim.value == null)) return false
      } else if (type === 'fact_list' && ['baseinfo', 'holder', 'invest', 'change', 'branch', 'other'].includes(group)) {
        if (claim.dimension !== 'company_profile') return false
        const path = claim.fact_path || ''
        const pathDomain = path.startsWith('profile.') || path.startsWith('counts.') ? path.split('.')[1]?.toLowerCase() : ''
        const expectedDomain = group === 'change' ? 'changeinfo' : group
        if (group === 'other' ? !path.startsWith('profile.') && !path.startsWith('counts.') : pathDomain !== expectedDomain) return false
      } else if (type === 'fact_list' || type === 'expandable_list') {
        if (claim.dimension !== group) return false
      } else {
        return false
      }
      usedClaims.add(index)
    }
    if (claimIndexes.length === 0) return false
  } else if (['field_group', 'expandable_list', 'fact_list'].includes(type)) {
    return false
  }

  if (type === 'overview' && group !== 'summary') return false
  if (type === 'limitation_list' && group !== 'limitations') return false
  if (type === 'status_list' && group !== 'boundaries') return false

  if (limitationIndexes !== undefined) {
    if (!validIndexList(limitationIndexes, answer.limitations.length)) return false
    if (limitationIndexes.some(index => index >= answer.limitations.length)) return false
    if (new Set(limitationIndexes).size !== limitationIndexes.length) return false
  }
  if (type === 'limitation_list' && (!Array.isArray(limitationIndexes) || limitationIndexes.length === 0)) return false

  if (value.s !== undefined) {
    if (type !== 'status_list' || !Array.isArray(value.s) || value.s.length > 10) return false
    const seenStatuses = new Set<string>()
    for (const entry of value.s) {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== 'string' || typeof entry[1] !== 'string') return false
      const domainLabel = PROFILE_DOMAIN_LABELS[entry[0]]
      const statusLabel = PROFILE_STATUS_LABELS[entry[1]]
      const key = `${entry[0]}:${entry[1]}`
      if (seenStatuses.has(key) || !domainLabel || !statusLabel) return false
      seenStatuses.add(key)
      const selectedLimitations = (Array.isArray(limitationIndexes) ? limitationIndexes : [])
        .map(index => answer.limitations[index]).filter(Boolean)
      const statusText = normalizedStatusText(`${domainLabel}${statusLabel}`)
      if (![fallbackMarkdown, ...selectedLimitations].some(text => normalizedStatusText(text).includes(statusText))) return false
    }
  }
  if (value.u !== undefined) {
    if (!Array.isArray(value.u) || value.u.length > 10 || value.u.some(item => typeof item !== 'string' || item.length > 300 || !fallbackMarkdown.includes(item))) return false
  }
  if (type === 'status_list' && !((Array.isArray(value.s) && value.s.length > 0)
    || (Array.isArray(limitationIndexes) && limitationIndexes.length > 0)
    || (Array.isArray(value.u) && value.u.length > 0))) return false
  return true
}

export function parseAnswerLayout(
  content: string,
  answer: AgentAnswer | undefined,
  evidence: AgentEvidenceRecord[] = [],
): ParsedAnswerLayout | null {
  if (!answer || answer.summary !== content || answer.status === 'failed') return null
  const matches = [...content.matchAll(/\[\/\/\]: # "ss-answer-layout:v1:/g)]
  if (matches.length !== 1) return null
  const marker = MARKER_PATTERN.exec(content)
  if (!marker) return null
  const fallbackMarkdown = content.replace(MARKER_PATTERN, '').trim()
  let parsed: unknown
  try {
    parsed = decodePayload(marker[1])
  } catch {
    return null
  }
  if (!isRecord(parsed) || !hasOnlyKeys(parsed, ['v', 'b']) || parsed.v !== 1 || !Array.isArray(parsed.b) || parsed.b.length === 0 || parsed.b.length > 40) return null
  const usedClaims = new Set<number>()
  const usedLimitations = new Set<number>()
  const seenBlocks = new Set<string>()
  const blocks: AnswerLayoutBlock[] = []
  for (const block of parsed.b) {
    if (!validateBlock(block, answer, evidence, fallbackMarkdown, usedClaims)) return null
    const blockKey = `${block.t}:${block.g}`
    if (seenBlocks.has(blockKey)) return null
    seenBlocks.add(blockKey)
    if (block.l) {
      if (block.l.some(index => usedLimitations.has(index))) return null
      block.l.forEach(index => usedLimitations.add(index))
    }
    blocks.push(block)
  }

  const profileLayout = answer.claims.length > 0 && answer.claims.every(claim => (
    claim.dimension === 'company_profile'
    && (claim.fact_path?.startsWith('profile.') || claim.fact_path?.startsWith('counts.'))
  ))
  const expectedClaims: number[] = []
  for (let index = 0; index < answer.claims.length; index += 1) {
    const claim = answer.claims[index]
    if (claim.validation_status !== 'supported' || claim.evidence_refs.length === 0
      || !claim.evidence_refs.some(reference => answer.evidence_refs.includes(reference))) continue
    if (!claimIsEvidenceBound(index, answer, evidence)) return null
    if (profileLayout && !fallbackMarkdown.includes(claim.statement)) continue
    expectedClaims.push(index)
  }
  if (expectedClaims.length !== usedClaims.size || expectedClaims.some(index => !usedClaims.has(index))) return null

  const expectedLimitations = answer.limitations.flatMap((limitation, index) => (
    !profileLayout || fallbackMarkdown.includes(limitation) ? [index] : []
  ))
  if (expectedLimitations.length !== usedLimitations.size || expectedLimitations.some(index => !usedLimitations.has(index))) return null

  const expectedStatuses: string[] = []
  if (profileLayout) {
    const statusSource = normalizedStatusText([
      fallbackMarkdown,
      ...expectedLimitations.map(index => answer.limitations[index]),
    ].join(' '))
    for (const [domain, domainLabel] of Object.entries(PROFILE_DOMAIN_LABELS)) {
      for (const [statusCode, statusLabel] of Object.entries(PROFILE_STATUS_LABELS)) {
        if (statusSource.includes(normalizedStatusText(`${domainLabel}${statusLabel}`))) {
          expectedStatuses.push(`${domain}:${statusCode}`)
        }
      }
    }
  }
  const actualStatuses = blocks.flatMap(block => block.s?.map(([domain, status]) => `${domain}:${status}`) || [])
  if (expectedStatuses.length !== actualStatuses.length
    || expectedStatuses.some(status => !actualStatuses.includes(status))) return null

  const expectedUncertainties = profileLayout
    ? fallbackMarkdown.split(/[。！？；;\n]+/).map(item => item.replace(/^\s*[-+>#]+\s*/, '').trim()).filter(item => (
      item && ['无法确认', '无法判断', '尚不明确', '不确定', '待核实', '需核实', '可能', '疑似', '是否'].some(marker => item.includes(marker))
      && ['重大诉讼', '诉讼', '行政处罚', '制裁', '失信', '经营异常', '负面舆情', '负面新闻', '风险信号'].some(topic => item.includes(topic))
    ))
    : []
  const actualUncertainties = blocks.flatMap(block => block.u || [])
  if (expectedUncertainties.length !== actualUncertainties.length
    || expectedUncertainties.some(item => !actualUncertainties.includes(item))) return null
  if (profileLayout) {
    const hasBoundaryContent = expectedStatuses.length > 0 || expectedLimitations.length > 0 || expectedUncertainties.length > 0
    if (hasBoundaryContent && !blocks.some(block => block.t === 'status_list')) return null
  } else if (!blocks.some(block => block.t === 'overview')) {
    return null
  }
  if (!blocks.some(block => block.t === 'overview' || block.t === 'field_group' || block.t === 'expandable_list' || block.t === 'fact_list' || block.t === 'status_list' || block.t === 'limitation_list')) return null
  return { layout: { v: 1, b: blocks }, fallbackMarkdown }
}

export function claimReferenceIsVisible(index: number, answer: AgentAnswer, evidence: AgentEvidenceRecord[]): boolean {
  return claimIsEvidenceBound(index, answer, evidence)
}
