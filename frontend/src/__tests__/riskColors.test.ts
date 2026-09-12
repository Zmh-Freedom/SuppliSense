import { describe, expect, it } from 'vitest';
import { getRiskBg, getRiskColor, getRiskLevel } from '../riskColors';

describe('risk safety score colors', () => {
  it('marks a high safety score as low risk in green', () => {
    expect(getRiskLevel(85)).toBe('低风险');
    expect(getRiskColor(85)).toBe('#2d8c63');
    expect(getRiskBg(85)).toBe('#ecfdf5');
  });

  it('marks a low safety score as high risk in red', () => {
    expect(getRiskLevel(10)).toBe('高风险');
    expect(getRiskColor(10)).toBe('#e06060');
    expect(getRiskBg(10)).toBe('#fef5f5');
  });
});
