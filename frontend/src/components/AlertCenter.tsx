import { useState } from 'react';
import { useQueryClient, useMutation } from '@tanstack/react-query';
import { api } from '../api';
import { useAlertHistory } from '../hooks';
import { queryKeys } from '../query-keys';
import Skeleton from './Skeleton';
import type { AlertDoc } from '../types';

export default function AlertCenter() {
  const queryClient = useQueryClient();
  const { data: alerts, isLoading, error, refetch } = useAlertHistory();
  const [checking, setChecking] = useState(false);

  const clearMutation = useMutation({
    mutationFn: () => api.delete('/alert/history'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory }),
  });

  const alertList = alerts ?? [];

  if (isLoading) {
    return (
      <div className="max-w-2xl mx-auto py-6 px-4 space-y-3">
        {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto py-20 text-center">
        <p className="text-gray-400 mb-4">加载失败，请检查后端服务</p>
        <button onClick={() => refetch()} className="text-sm text-blue-500 hover:text-blue-600">重试</button>
      </div>
    );
  }

  if (alertList.length === 0) {
    return (
      <div className="max-w-2xl mx-auto py-10 text-center">
        <p className="text-gray-300 text-lg mb-4">暂无告警</p>
        <button
          onClick={() => { setChecking(true); api.post('/alert/check-all').then(() => refetch()).catch(() => {}).finally(() => setChecking(false)); }}
          disabled={checking}
          className="bg-[#333] text-white rounded-xl px-5 py-2 text-sm hover:bg-[#555] disabled:opacity-50"
        >
          {checking ? '巡检中…' : '立即巡检'}
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-4 px-4">
      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-gray-400">{alertList.length} 条告警</p>
        <button onClick={() => clearMutation.mutate()} disabled={clearMutation.isPending} className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50">{clearMutation.isPending ? '清空中…' : '清空记录'}</button>
      </div>

      {alertList.map((doc: AlertDoc, i: number) => {
        const isCritical = doc.severity === 'critical';
        return (
          <div
            key={i}
            className={`mb-2 rounded-xl border bg-white p-4 shadow-sm ${
              isCritical ? 'border-l-[3px] border-l-[#e06060]' : 'border-l-[3px] border-l-[#d4a040]'
            } border-[#e8e8e3]`}
          >
            <div className="flex justify-between items-baseline">
              <span className="text-sm font-semibold">
                <span style={{ color: isCritical ? '#e06060' : '#d4a040' }}>● </span>
                {doc.company_name}
              </span>
              <span className="text-xs text-gray-400">{doc.created_at.slice(0, 16).replace('T', ' ')}</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {doc.changes.map(c => `${c.field} ${c.old} → ${c.new}`).join(' · ')}
            </p>
          </div>
        );
      })}
    </div>
  );
}
