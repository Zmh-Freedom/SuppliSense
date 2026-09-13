import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import RiskSummary from '../components/RiskSummary';

describe('RiskSummary', () => {
  it('keeps an unavailable score neutral instead of presenting it as high risk', () => {
    render(<RiskSummary score={null} level="未知" />);

    expect(screen.getByText('暂无法判断')).toBeInTheDocument();
    expect(screen.getByText('暂无风险快照，暂无法判断风险等级')).toBeInTheDocument();
    expect(screen.getByLabelText('风险摘要')).toHaveStyle({ background: '#f5f5f4' });
  });
});
