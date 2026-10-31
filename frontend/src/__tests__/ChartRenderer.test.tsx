import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ChartRenderer from '../components/ChartRenderer';

describe('ChartRenderer line charts', () => {
  it('leaves missing dates as gaps and exposes exact values in an accessible table', () => {
    render(<ChartRenderer lineIsContinuous missingValueLabel="本月快照未取得" data={{
      type: 'line',
      title: '收货记录数（条）',
      data: [
        { date: '2026-01', value: 100 },
        { date: '2026-02', value: 102 },
        { date: '2026-03', value: null },
        { date: '2026-04', value: 103 },
        { date: '2026-05', value: 104 },
      ],
      source: 'tool',
    }} />);

    const chart = screen.getByRole('img', { name: /收货记录数.*趋势图/ });
    expect(chart.querySelectorAll('polyline')).toHaveLength(2);
    expect(Array.from(chart.querySelectorAll('text')).some(label => label.textContent === '0')).toBe(true);

    fireEvent.click(screen.getByText('查看日期与数值'));
    const table = screen.getByRole('table', { name: /收货记录数/ });
    expect(within(table).getByRole('row', { name: /2026-01.*100/ })).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /2026-03.*本月快照未取得/ })).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /2026-05.*104/ })).toBeInTheDocument();
  });

  it('shows the accessible table without connecting dates when continuity is unknown', () => {
    render(<ChartRenderer data={{
      type: 'line',
      title: '财务收入',
      data: [
        { date: '2024', value: 100 },
        { date: '2026', value: 120 },
      ],
    }} />);

    expect(screen.queryByRole('img', { name: /财务收入.*趋势图/ })).not.toBeInTheDocument();
    expect(screen.getByText(/无法确认日期连续性，折线未绘制/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('查看日期与数值'));
    const table = screen.getByRole('table', { name: /财务收入/ });
    expect(within(table).getByRole('row', { name: /2024.*100/ })).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /2026.*120/ })).toBeInTheDocument();
  });

  it('keeps dates accessible when every period lacks a value', () => {
    render(<ChartRenderer data={{
      type: 'line',
      title: '月度结算',
      data: [
        { date: '2026-09', value: null },
        { date: '2026-10', value: null },
      ],
    }} />);

    expect(screen.queryByRole('img', { name: /月度结算.*趋势图/ })).not.toBeInTheDocument();
    expect(screen.getByText(/没有可绘制数值/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('查看日期与数值'));
    const table = screen.getByRole('table', { name: /月度结算/ });
    expect(within(table).getByRole('row', { name: /2026-09.*未提供数值/ })).toBeInTheDocument();
    expect(within(table).getByRole('row', { name: /2026-10.*未提供数值/ })).toBeInTheDocument();
  });
});
