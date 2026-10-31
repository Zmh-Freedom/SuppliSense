import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AgentAnswer, AgentEvidenceRecord } from '../types'
import AssistantRichText from '../components/AssistantRichText'

function layoutMarker(layout: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(layout))
  let binary = ''
  bytes.forEach(byte => { binary += String.fromCharCode(byte) })
  const encoded = btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
  return `[//]: # "ss-answer-layout:v1:${encoded}"`
}

function profileLayoutAnswer(): AgentAnswer {
  const profileEvidence = ['profile-evidence']
  const statements = ['张三', '李四', '王五', '赵六', '钱七', '孙八', '周九']
  return {
    status: 'partial',
    summary: [
      [
        '工商资料：示例企业工商资料摘要（此文本仅作为旧客户端回退）；对外投资：未查询；对外投资未查询，当前无法确认其情况；重大诉讼情况无法确认。',
        '示例企业有限公司注册资本：5000万元',
        ...statements.map(name => `示例企业有限公司股东：${name}`),
      ].join('\n'),
      layoutMarker({ v: 1, b: [
        { t: 'field_group', g: 'baseinfo', c: [0] },
        { t: 'expandable_list', g: 'holder', c: [1, 2, 3, 4, 5, 6, 7] },
        { t: 'status_list', g: 'boundaries', s: [['invest', 'not_queried']], l: [0], u: ['重大诉讼情况无法确认'] },
      ] }),
    ].join('\n\n'),
    claims: [
      {
        claim_id: 'capital', entity_id: 'company-1', dimension: 'company_profile',
        statement: '示例企业有限公司注册资本：5000万元', value: '5000万元', fact_path: 'profile.baseinfo.regCapital',
        unit: null, evidence_refs: profileEvidence, confidence: 0.9, validation_status: 'supported', validation_reasons: [],
      },
      ...statements.map((name, index) => ({
        claim_id: `holder-${index}`, entity_id: 'company-1', dimension: 'company_profile',
        statement: `示例企业有限公司股东：${name}`, value: name, fact_path: 'profile.holder_names',
        unit: null, evidence_refs: profileEvidence, confidence: 0.9, validation_status: 'supported' as const, validation_reasons: [],
      })),
    ],
    limitations: ['对外投资未查询，当前无法确认其情况'],
    action_proposals: [],
    action_receipts: [],
    evidence_refs: profileEvidence,
  }
}

function profileEvidence(): AgentEvidenceRecord[] {
  return [{
    evidence_id: 'profile-evidence',
    entity_id: 'company-1',
    dimension: 'company_profile',
    provider: 'fixture',
    source_type: 'profile_snapshot',
    status: 'available',
    data_mode: 'formal',
    collected_at: '2026-10-30T00:00:00Z',
  }]
}

describe('AssistantRichText', () => {
  it('renders procurement answer hierarchy instead of flattening markdown', () => {
    render(<AssistantRichText
      label="采购分析"
      content={'## 结论\n\n**利润承压。**\n\n- 净利润同比下降\n- 建议核查现金流'}
    />)

    expect(screen.getByText('采购分析')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '结论' })).toBeInTheDocument()
    expect(screen.getByText('利润承压。')).toHaveClass('font-semibold')
    expect(screen.getByRole('list')).toBeInTheDocument()
  })

  it('renders links and tables with readable interaction affordances', () => {
    render(<AssistantRichText content={'[查看来源](https://example.com)\n\n| 指标 | 当前值 |\n| --- | --- |\n| 资产负债率 | 43% |'} />)

    expect(screen.getByRole('link', { name: '查看来源' })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '指标' })).toBeInTheDocument()
  })

  it('renders profile Markdown while keeping raw HTML inert', () => {
    const { container } = render(<AssistantRichText content={[
      '### 企业基本信息',
      '',
      '- **法定代表人：张敏**',
      '',
      '<img src=x onerror=alert(1) />',
    ].join('\n')} />)

    expect(screen.getByRole('heading', { name: '企业基本信息' })).toBeInTheDocument()
    expect(screen.getByRole('listitem')).toHaveTextContent('法定代表人：张敏')
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1) />')
  })

  it('maps a verified layout to compact fields, expandable shareholder lists, and visible boundaries', () => {
    const answer = profileLayoutAnswer()
    render(<AssistantRichText content={answer.summary} answer={answer} evidence={profileEvidence()} />)

    expect(screen.getByRole('group', { name: '企业基本信息' })).toBeInTheDocument()
    expect(screen.getByText('注册资本')).toBeInTheDocument()
    expect(screen.getByText('5000万元')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '股东信息' })).toBeInTheDocument()
    expect(screen.getByText('查看其余 2 位股东')).toBeInTheDocument()
    expect(screen.queryByText(/旧客户端回退/)).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: '资料状态与限制' })).toHaveTextContent('对外投资未查询，当前无法确认其情况')
    expect(screen.getAllByText(/对外投资.*未查询/)).toHaveLength(1)
    expect(screen.getByText('对外投资未查询，当前无法确认其情况')).toBeInTheDocument()
  })

  it('deduplicates exact status limitations and folds a confirmed-empty count into its status', () => {
    const source = profileLayoutAnswer()
    const holderClaims = source.claims.slice(1)
    const branchClaim = {
      ...source.claims[0],
      claim_id: 'branch-count',
      statement: '示例企业有限公司分支机构记录数：0',
      value: 0,
      fact_path: 'counts.branch',
      evidence_refs: ['branch-evidence'],
    }
    const limitations = ['对外投资：未查询', '历史变更：未查询', '工商资料来源仅部分成功']
    const statements = [source.claims[0].statement, ...holderClaims.map(claim => claim.statement), branchClaim.statement]
    const fallback = [
      '工商资料',
      ...statements,
      ...limitations,
      '对外投资：未查询',
      '历史变更：未查询',
      '分支机构：明确无记录',
      '重大诉讼情况无法确认',
    ].join('\n')
    const claims = [...source.claims, branchClaim]
    const summary = [fallback, layoutMarker({ v: 1, b: [
      { t: 'field_group', g: 'baseinfo', c: [0] },
      { t: 'expandable_list', g: 'holder', c: [1, 2, 3, 4, 5, 6, 7] },
      { t: 'fact_list', g: 'branch', c: [8] },
      { t: 'status_list', g: 'boundaries', s: [
        ['invest', 'not_queried'], ['change', 'not_queried'], ['branch', 'no_records'],
      ], l: [0, 1, 2], u: ['重大诉讼情况无法确认'] },
    ] })].join('\n\n')
    const answer: AgentAnswer = {
      ...source,
      summary,
      claims,
      limitations,
      evidence_refs: ['profile-evidence', 'branch-evidence'],
    }
    const evidence = [
      ...profileEvidence(),
      {
        evidence_id: 'branch-evidence', entity_id: 'company-1', dimension: 'company_profile',
        provider: 'fixture', source_type: 'profile_snapshot', status: 'confirmed_empty' as const,
        data_mode: 'formal' as const, collected_at: '2026-10-30T00:00:00Z',
      },
    ]

    render(<AssistantRichText content={answer.summary} answer={answer} evidence={evidence} />)

    expect(screen.getAllByText('对外投资：未查询')).toHaveLength(1)
    expect(screen.getAllByText('历史变更：未查询')).toHaveLength(1)
    expect(screen.getByText('工商资料来源仅部分成功')).toBeInTheDocument()
    expect(screen.getAllByText(/分支机构.*明确无记录/)).toHaveLength(1)
    expect(screen.queryByText('示例企业有限公司分支机构记录数：0')).not.toBeInTheDocument()
    expect(answer.claims.find(claim => claim.claim_id === 'branch-count')?.value).toBe(0)
  })

  it('keeps the holder count in the summary and expands only shareholder names', () => {
    const source = profileLayoutAnswer()
    const markerPattern = /\n\n\[\/\/\]: # "ss-answer-layout:v1:[A-Za-z0-9_-]+"\s*$/
    const fallback = source.summary.replace(markerPattern, '')
    const countClaim = {
      ...source.claims[1],
      claim_id: 'holder-count',
      statement: '示例企业有限公司股东信息记录数：11',
      value: 11,
      fact_path: 'counts.holder',
    }
    const claims = [source.claims[0], countClaim, ...source.claims.slice(1)]
    const summary = [
      fallback.replace('示例企业有限公司注册资本：5000万元', [
        '示例企业有限公司注册资本：5000万元',
        countClaim.statement,
      ].join('\n')),
      layoutMarker({ v: 1, b: [
        { t: 'field_group', g: 'baseinfo', c: [0] },
        { t: 'expandable_list', g: 'holder', c: claims.slice(1).map((_, index) => index + 1) },
        { t: 'status_list', g: 'boundaries', s: [['invest', 'not_queried']], l: [0], u: ['重大诉讼情况无法确认'] },
      ] }),
    ].join('\n\n')
    const answer: AgentAnswer = { ...source, claims, summary }

    render(<AssistantRichText content={answer.summary} answer={answer} evidence={profileEvidence()} />)

    expect(screen.getByRole('group', { name: '股东信息' })).toHaveTextContent('股东信息记录数：11')
    expect(screen.getByText('查看其余 2 位股东')).toBeInTheDocument()
    expect(screen.queryByText('示例企业有限公司股东信息记录数：11')).not.toBeInTheDocument()
  })

  it('does not activate layout markers in plain text or when claim references are invalid', () => {
    const answer = profileLayoutAnswer()
    const markerOnly = layoutMarker({ v: 1, b: [{ t: 'field_group', g: 'baseinfo', c: [999] }] })
    const invalidAnswer = { ...answer, summary: `旧版回答\n\n${markerOnly}` }
    const { rerender } = render(<AssistantRichText content={invalidAnswer.summary} />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText('旧版回答')).toBeInTheDocument()

    rerender(<AssistantRichText content={invalidAnswer.summary} answer={invalidAnswer} evidence={profileEvidence()} />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText('旧版回答')).toBeInTheDocument()
  })

  it('falls back to the full Markdown answer when a forged layout omits states or limitations', () => {
    const source = profileLayoutAnswer()
    const fallback = source.summary.replace(/\n\n\[\/\/\]: # "ss-answer-layout:v1:[A-Za-z0-9_-]+"\s*$/, '')
    const answer = {
      ...source,
      summary: `${fallback}\n\n${layoutMarker({ v: 1, b: [
        { t: 'field_group', g: 'baseinfo', c: [0] },
        { t: 'expandable_list', g: 'holder', c: [1, 2, 3, 4, 5, 6, 7] },
      ] })}`,
    }
    render(<AssistantRichText content={answer.summary} answer={answer} evidence={profileEvidence()} />)

    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()
    expect(screen.getByText(/对外投资：未查询/)).toBeInTheDocument()
    expect(screen.getByText(/重大诉讼情况无法确认/)).toBeInTheDocument()
  })

  it('rejects the entire layout when claim coverage or evidence binding is incomplete', () => {
    const source = profileLayoutAnswer()
    const fallback = source.summary.replace(/\n\n\[\/\/\]: # "ss-answer-layout:v1:[A-Za-z0-9_-]+"\s*$/, '')
    const baseBlocks = [
      { t: 'field_group', g: 'baseinfo', c: [0] },
      { t: 'expandable_list', g: 'holder', c: [1, 2, 3, 4, 5, 6, 7] },
      { t: 'status_list', g: 'boundaries', s: [['invest', 'not_queried']], l: [0], u: ['重大诉讼情况无法确认'] },
    ]
    const malformedLayouts = [
      // A supported claim disappears from the component tree.
      [baseBlocks[0], { ...baseBlocks[1], c: [1, 2, 3, 4, 5, 6] }, baseBlocks[2]],
      // A duplicated claim/block is rejected as one invalid layout.
      [...baseBlocks, baseBlocks[0]],
      // Unknown payload fields never reach a renderer.
      [{ ...baseBlocks[0], href: 'javascript:alert(1)' }, baseBlocks[1], baseBlocks[2]],
      // A status block that drops an answer limitation is incomplete.
      [baseBlocks[0], baseBlocks[1], { ...baseBlocks[2], l: [] }],
    ]

    const { rerender } = render(<AssistantRichText content={source.summary} answer={source} evidence={profileEvidence()} />)
    for (const blocks of malformedLayouts) {
      const invalidAnswer = { ...source, summary: `${fallback}\n\n${layoutMarker({ v: 1, b: blocks })}` }
      rerender(<AssistantRichText content={invalidAnswer.summary} answer={invalidAnswer} evidence={profileEvidence()} />)
      expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
      expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()
      expect(screen.getByText(/对外投资：未查询/)).toBeInTheDocument()
    }

    rerender(<AssistantRichText content={source.summary} answer={source} evidence={[]} />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()

    rerender(<AssistantRichText
      content={source.summary}
      answer={source}
      evidence={profileEvidence().map(record => ({ ...record, data_mode: 'synthetic' }))}
    />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()

    rerender(<AssistantRichText
      content={source.summary}
      answer={source}
      evidence={profileEvidence().map(record => ({ ...record, valid_until: '2020-01-01T00:00:00Z' }))}
    />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()

    const unsupportedAnswer = {
      ...source,
      claims: source.claims.map((claim, index) => index === 2 ? { ...claim, validation_status: 'unsupported' as const } : claim),
    }
    rerender(<AssistantRichText content={unsupportedAnswer.summary} answer={unsupportedAnswer} evidence={profileEvidence()} />)
    expect(screen.queryByRole('group', { name: '企业基本信息' })).toBeNull()
    expect(screen.getByText(/示例企业工商资料摘要/)).toBeInTheDocument()
  })
})
