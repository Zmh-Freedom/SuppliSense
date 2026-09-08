import type { ReactNode } from 'react';

export function ConclusionSummary({ children }: { children: ReactNode }) {
  return <section aria-label="结论摘要">{children}</section>;
}

export function EvidenceTable({ children }: { children: ReactNode }) {
  return <section aria-label="证据表">{children}</section>;
}

export function TrendSection({ children }: { children: ReactNode }) {
  return <section aria-label="趋势图">{children}</section>;
}

export function ReviewChecklist({ children }: { children: ReactNode }) {
  return <section aria-label="复核清单">{children}</section>;
}
