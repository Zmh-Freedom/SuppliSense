import { useMemo } from 'react';
import type { AgentTraceEvent } from '../types';

const PRIVATE_KEYS = new Set([
  'analysis', 'chain_of_thought', 'cot', 'messages', 'model_output', 'prompt',
  'raw_response', 'reasoning', 'system_prompt', 'thought', 'thoughts',
]);

const TASK_LABELS: Record<string, string> = {
  completed: '已完成', failed: '失败', pending: '待执行', running: '执行中',
};

const TRACE_KEY_LABELS: Record<string, string> = {
  run_id: '任务编号', status: '处理阶段', stage: '处理阶段', count: '数量',
  source: '来源', source_stage: '来源阶段', provider: '数据服务',
  pending_review_ids: '待复核候选', failed_dimensions: '未完成维度',
  missing_requirements: '缺少条件', conflicting_requirements: '冲突条件',
  checksum: '规则校验码', node: '处理环节', task_count: '子任务数量', task_ids: '处理事项',
  target_supplier_names: '供应商范围', analysis_dimensions: '分析范围', stop_reason: '结束原因',
  tool_call_count: '查询次数', iteration: '补全轮次', max_iterations: '最多轮次',
};

const TRACE_VALUE_LABELS: Record<string, string> = {
  CREATED: '已创建', CLARIFYING: '等待补充寻源条件', POLICY_LOCKED: '规则已锁定',
  LOCAL_SEARCHING: '正在检索本地候选', EXTERNAL_REVIEW: '外部候选待核验',
  IDENTITY_RESOLVING: '正在核验企业主体', IDENTITY_REVIEW: '等待确认企业主体',
  INVESTIGATING: '正在调查风险证据', EVIDENCE_REVIEW: '等待复核风险证据',
  SCORING: '正在形成候选决策', READY_FOR_REVIEW: '等待采购复核',
  ACTION_PENDING: '等待操作审批', ACTION_EXECUTING: '正在执行批准操作',
  COMPLETED: '已完成', PARTIAL: '部分完成', NEEDS_REVIEW: '需要人工复核',
  ACTION_FAILED: '操作失败', FAILED: '任务失败', CANCELLED: '已取消',
  evidence_sufficient: '证据已足够', no_remediation_spec: '没有可执行补救方案',
  load_run: '读取寻源任务', parse_requirement: '解析寻源条件', local_discovery: '检索历史合作候选',
  external_discovery: '检索外部候选', identity: '主体身份', risk: '风险', esg: '可持续性',
};

function traceKeyLabel(key: string): string {
  return TRACE_KEY_LABELS[key] ?? key;
}

function traceValueLabel(value: string): string {
  return TRACE_VALUE_LABELS[value] ?? value;
}

function asText(value: unknown): string | null {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null;
}

function valueList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(asText).filter((item): item is string => item !== null)
    : [];
}

function safeSummary(data: Record<string, unknown>): string[] {
  return Object.entries(data)
    .filter(([key]) => !PRIVATE_KEYS.has(key.toLowerCase()))
    .flatMap(([key, value]) => {
      const text = asText(value);
      if (text !== null && text) return [`${traceKeyLabel(key)}：${traceValueLabel(text)}`];
      if (Array.isArray(value) && value.length > 0) return [`${traceKeyLabel(key)}：${valueList(value).map(traceValueLabel).join('、')}`];
      return [];
    })
    .slice(0, 4);
}

function taskProgress(events: AgentTraceEvent[]): { total: number; completed: number; failed: number; taskIds: string[]; statuses: Map<string, string> } {
  const planned = events.findLast(event => event.kind === 'plan_created');
  const taskIds = valueList(planned?.data.task_ids);
  const terminal = new Map<string, string>();
  for (const event of events) {
    const taskId = asText(event.data.task_id);
    const resultStatus = asText(event.data.result_status);
    if (taskId && resultStatus) terminal.set(taskId, resultStatus);
  }
  const total = Number(planned?.data.task_count) || taskIds.length || terminal.size;
  const completed = [...terminal.values()].filter(status => status === 'completed').length;
  const failed = [...terminal.values()].filter(status => status !== 'completed').length;
  return { total, completed, failed, taskIds, statuses: terminal };
}

function executionScope(events: AgentTraceEvent[]): { targets: string[]; dimensions: string[] } {
  const targets = new Set<string>();
  const dimensions = new Set<string>();
  for (const event of events) {
    for (const key of ['company_name', 'supplier_name', 'target', 'target_company', 'target_supplier_names']) {
      if (Array.isArray(event.data[key])) {
        for (const value of valueList(event.data[key])) targets.add(value);
        continue;
      }
      const value = asText(event.data[key]);
      if (value) targets.add(value);
    }
    for (const key of ['dimension', 'dimensions', 'analysis_dimension', 'analysis_dimensions']) {
      for (const value of Array.isArray(event.data[key]) ? valueList(event.data[key]) : [asText(event.data[key])].filter((item): item is string => item !== null)) dimensions.add(value);
    }
  }
  return { targets: [...targets].slice(-6), dimensions: [...dimensions].slice(-6).map(traceValueLabel) };
}

function issueSummary(events: AgentTraceEvent[]): string[] {
  const issues: string[] = [];
  for (const event of events) {
    const data = event.data;
    if (event.kind === 'subtask_failed') issues.push(`子任务未完成：${asText(data.task_id) ?? '未命名任务'}`);
    for (const field of ['failed_dimensions', 'missing_requirements', 'conflicting_requirements']) {
      const items = valueList(data[field]);
      if (items.length > 0) issues.push(`${traceKeyLabel(field)}：${items.map(traceValueLabel).join('、')}`);
    }
    if (event.kind === 'validator_completed' && data.can_recommend === false) {
      issues.push(`证据校验未达到推荐门槛${asText(data.stop_reason) ? `：${asText(data.stop_reason)}` : ''}`);
    }
  }
  return [...new Set(issues)];
}

export default function AgentExecutionTrace({ events }: { events: AgentTraceEvent[] }) {
  const progress = useMemo(() => taskProgress(events), [events]);
  const issues = useMemo(() => issueSummary(events), [events]);
  const latestLoop = useMemo(() => [...events].reverse().find(event => event.kind === 'loop_evaluated'), [events]);
  const scope = useMemo(() => executionScope(events), [events]);

  if (events.length === 0) {
    return <section aria-labelledby="agent-trace-heading" className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 space-y-2">
      <h3 id="agent-trace-heading" className="text-sm font-semibold text-[var(--color-text)]">执行追踪</h3>
      <p className="text-sm leading-6 text-[var(--color-text-secondary)]">暂未收到可展示的执行事件。任务开始后，这里会显示任务进度、证据校验与人工审批状态。</p>
    </section>;
  }

  const percent = progress.total > 0 ? Math.min(100, Math.round(((progress.completed + progress.failed) / progress.total) * 100)) : 0;
  const loopData = latestLoop?.data ?? {};
  return <section aria-labelledby="agent-trace-heading" className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h3 id="agent-trace-heading" className="text-sm font-semibold text-[var(--color-text)]">执行追踪</h3><p className="text-xs leading-5 text-[var(--color-text-secondary)]">仅展示任务状态、工具摘要与证据结论，不展示模型推理内容。</p></div>
      <span className="rounded-full bg-[var(--color-surface-hover)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)]">{events.length} 条事件</span>
    </div>
    <div className="space-y-2">
      <div className="flex justify-between gap-3 text-xs text-[var(--color-text-secondary)]"><span>任务矩阵：{progress.total > 0 ? `${progress.completed + progress.failed}/${progress.total}` : '等待计划生成'}</span><span>{percent}%</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--color-surface-hover)]"><div className="h-full rounded-full bg-[var(--color-primary-bg)] transition-[width] duration-200 motion-reduce:transition-none" style={{ width: `${percent}%` }} /></div>
      {progress.taskIds.length > 0 && <div className="flex flex-wrap gap-1.5">{progress.taskIds.map(taskId => <span key={taskId} className="rounded-md bg-[var(--color-surface-hover)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">{taskId} · {TASK_LABELS[progress.statuses.get(taskId) ?? 'pending'] ?? '待执行'}</span>)}</div>}
    </div>
    {(scope.targets.length > 0 || scope.dimensions.length > 0) && <div className="grid gap-2 sm:grid-cols-2"><div className="rounded-xl border border-[var(--color-border)] p-3"><p className="text-xs font-medium text-[var(--color-text)]">当前目标企业</p><p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{scope.targets.length > 0 ? scope.targets.join('、') : '事件尚未提供企业范围'}</p></div><div className="rounded-xl border border-[var(--color-border)] p-3"><p className="text-xs font-medium text-[var(--color-text)]">分析维度</p><p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{scope.dimensions.length > 0 ? scope.dimensions.join('、') : '事件尚未提供分析维度'}</p></div></div>}
    {latestLoop && <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-hover)] p-3 text-xs text-[var(--color-text-secondary)]"><p className="font-medium text-[var(--color-text)]">证据补全循环</p><p className="mt-1 leading-5">第 {asText(loopData.iteration) ?? '0'} / {asText(loopData.max_iterations) ?? '0'} 轮，工具调用 {asText(loopData.tool_call_count) ?? '0'} 次{asText(loopData.stop_reason) ? `；停止原因：${traceValueLabel(asText(loopData.stop_reason)!)}` : ''}</p></div>}
    {issues.length > 0 && <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-3"><p className="text-xs font-medium text-amber-900">需要关注</p><ul className="mt-1 space-y-1 text-xs leading-5 text-amber-800">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
    <ol className="space-y-2" aria-live="polite">{events.slice(-12).reverse().map(event => <li key={event.eventId} className="rounded-xl border border-[var(--color-border)] p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-[var(--color-text)]">{event.message}</p><span className="rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]">{traceValueLabel(event.status)}</span></div><p className="mt-1 text-xs text-[var(--color-text-secondary)]">{event.source === 'agent_trace' ? 'Agent 任务事件' : '工作流节点事件'}{event.atMs !== undefined ? ` · ${Math.round(event.atMs)} ms` : ''}</p>{safeSummary(event.data).length > 0 && <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{safeSummary(event.data).join('；')}</p>}</li>)}</ol>
  </section>;
}
