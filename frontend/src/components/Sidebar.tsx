import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import type { WatchlistData, AlertDoc } from '../types';

interface Props {
  onRefresh: () => void;
  onSelect?: (name: string) => void;
}

export default function Sidebar({ onRefresh, onSelect }: Props) {
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [alertCount, setAlertCount] = useState(0);
  const [newName, setNewName] = useState('');
  const [adding, setAdding] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<{ alerts: AlertDoc[] }>('/alert/history').then(d => setAlertCount(d.alerts.length)).catch(() => {});
    api.get<WatchlistData>('/alert/watchlist').then(d => setWatchlist(d.companies)).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const add = async () => {
    const name = newName.trim();
    if (!name || adding) return;
    setAdding(true);
    try {
      await api.post('/alert/watch', { company_name: name });
      setNewName('');
      await load();
      onRefresh();
    } catch {
      // 添加失败，静默处理
    } finally {
      setAdding(false);
    }
  };

  const remove = async (name: string) => {
    try {
      await api.delete('/alert/watch', { company_name: name });
      setHovered(null);
      await load();
      onRefresh();
    } catch {
      // 移除失败，静默处理
    }
  };

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await api.upload('/alert/watch/upload', file);
      await load();
      onRefresh();
    } catch {
      // 上传失败，静默处理
    }
  };

  return (
    <aside className="w-64 h-screen border-r border-[#e8e8e3] bg-[#f5f5f0] flex flex-col text-sm">
      {/* header */}
      <div className="px-4 pt-4 pb-2">
        <h2 className="text-sm font-semibold text-[#555] tracking-wide">供应商分析</h2>
      </div>

      {/* stats */}
      <div className="px-4 pb-3">
        <div className="flex gap-2">
          <div className="flex-1 bg-white rounded-xl border border-[#e8e8e3] px-3 py-2.5">
            <div className="text-xl font-bold text-[#333]">{watchlist.length}</div>
            <div className="text-[11px] text-gray-400 mt-0.5">监控中</div>
          </div>
          <div className="flex-1 bg-white rounded-xl border border-[#e8e8e3] px-3 py-2.5">
            <div className="text-xl font-bold text-[#333]">{alertCount}</div>
            <div className="text-[11px] text-gray-400 mt-0.5">告警</div>
          </div>
        </div>
      </div>

      {/* add form */}
      <div className="px-4 pb-2">
        <form onSubmit={e => { e.preventDefault(); add(); }} className="flex gap-1.5">
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="添加企业…"
            className="flex-1 border border-[#e8e8e3] rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-[#bbb] bg-white placeholder-gray-300"
          />
          <button
            type="submit"
            disabled={adding || !newName.trim()}
            className="bg-[#333] text-white rounded-lg px-3 py-1.5 text-xs hover:bg-[#555] disabled:opacity-30 transition-opacity shrink-0"
          >
            {adding ? '...' : '添加'}
          </button>
        </form>
      </div>

      {/* excel import */}
      <div className="px-4 pb-3">
        <label className="flex items-center justify-center gap-1.5 text-[11px] text-gray-400 cursor-pointer hover:text-gray-500 transition-colors border border-dashed border-[#e0e0d8] rounded-lg py-1.5">
          <span>📎 Excel 批量导入</span>
          <input type="file" accept=".xlsx" onChange={handleFile} className="hidden" />
        </label>
      </div>

      {/* divider */}
      <div className="border-t border-[#e8e8e3] mx-4" />

      {/* list header */}
      <div className="px-4 pt-3 pb-1 flex justify-between items-center">
        <span className="text-[11px] text-gray-400 uppercase tracking-wide">
          监控清单 {watchlist.length > 0 && `(${watchlist.length})`}
        </span>
      </div>

      {/* list */}
      <div className="flex-1 overflow-auto px-2 pb-2">
        {watchlist.length === 0 ? (
          <p className="text-[11px] text-gray-300 text-center mt-6 px-4">暂无监控企业，输入名称添加</p>
        ) : (
          watchlist.map(c => (
            <div
              key={c}
              className="group flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-white/60 transition-colors cursor-pointer"
              onMouseEnter={() => setHovered(c)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSelect?.(c)}
            >
              <span className="text-xs text-[#444] truncate flex-1">{c}</span>
              <button
                onClick={e => { e.stopPropagation(); remove(c); }}
                className={`text-gray-300 hover:text-red-400 text-sm leading-none transition-all shrink-0 ml-1 ${
                  hovered === c ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                }`}
                title="移除"
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>

      {/* actions */}
      <div className="border-t border-[#e8e8e3] px-4 py-3 space-y-2">
        <div className="flex gap-2">
          <button
            onClick={() => api.post('/alert/check-all').then(load).catch(() => {})}
            className="flex-1 border border-[#e8e8e3] bg-white rounded-lg py-1.5 text-[11px] text-[#555] hover:bg-[#f9f9f5] transition-colors"
          >
            ⚡ 免费巡检
          </button>
          <button
            onClick={() => api.post('/alert/refresh-all').then(load).catch(() => {})}
            className="flex-1 border border-[#e8e8e3] bg-white rounded-lg py-1.5 text-[11px] text-[#555] hover:bg-[#f9f9f5] transition-colors"
          >
            🔄 付费刷新
          </button>
        </div>
        <p className="text-[10px] text-gray-300 text-center">每日 9:00 免费 · 周一 9:00 付费</p>
      </div>
    </aside>
  );
}
