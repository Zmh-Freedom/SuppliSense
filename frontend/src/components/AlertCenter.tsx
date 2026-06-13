import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import type { AlertDoc } from '../types';

export default function AlertCenter() {
  const [alerts, setAlerts] = useState<AlertDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(false);
    api.get<{ alerts: AlertDoc[] }>('/alert/history', undefined, signal)
      .then(d => setAlerts(d.alerts))
      .catch((err) => { if (err.name !== 'AbortError') setError(true); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const clear = async () => {
    try {
      await api.delete('/alert/history');
      setAlerts([]);
    } catch {
      // 清空失败
    }
  };

  if (loading) {
    return <div className="max-w-2xl mx-auto py-10 text-center text-gray-300">加载中…</div>;
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto py-20 text-center">
        <p className="text-gray-400 mb-4">加载失败，请检查后端服务</p>
        <button onClick={() => load()} className="text-sm text-blue-500 hover:text-blue-600">重试</button>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <div className="max-w-2xl mx-auto py-10 text-center">
        <p className="text-gray-300 text-lg mb-4">暂无告警</p>
        <button
          onClick={() => api.post('/alert/check-all').then(() => load()).catch(() => {})}
          className="bg-[#333] text-white rounded-xl px-5 py-2 text-sm hover:bg-[#555]"
        >
          立即巡检
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-4 px-4">
      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-gray-400">{alerts.length} 条告警</p>
        <button onClick={clear} className="text-xs text-gray-400 hover:text-gray-600">清空记录</button>
      </div>

      {alerts.map((doc, i) => {
        const isCritical = doc.severity === 'critical';
        return (
          <div
            key={i}
            className={`mb-2 rounded-xl border bg-white p-4 ${
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
