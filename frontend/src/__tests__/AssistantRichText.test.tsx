import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AssistantRichText from '../components/AssistantRichText'

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
})
