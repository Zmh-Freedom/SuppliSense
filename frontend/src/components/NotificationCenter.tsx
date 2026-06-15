import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';

interface Notification {
  _id: string;
  type: string;
  title: string;
  message: string;
  company_name?: string;
  read: boolean;
  created_at: string;
}

export default function NotificationCenter() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.notifications(100),
    queryFn: () => api.get<{ notifications: Notification[]; unread_count: number }>('/notifications?limit=100'),
  });

  const markAllReadMutation = useMutation({
    mutationFn: () => api.put('/notifications/read-all'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications() }),
  });

  const notifs = data?.notifications ?? [];

  return (
    <div className="max-w-2xl mx-auto py-6 px-4">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-[#333]">通知中心</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            {notifs.filter(n => !n.read).length} 条未读 · 共 {notifs.length} 条
          </p>
        </div>
        <button onClick={() => markAllReadMutation.mutate()}
          className="text-xs text-[#333] hover:underline disabled:opacity-50"
          disabled={notifs.every(n => n.read) || markAllReadMutation.isPending}>
          全部标为已读
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-5 h-5 border-2 border-[#333] border-t-transparent rounded-full animate-spin" />
        </div>
      ) : notifs.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-12">暂无通知</p>
      ) : (
        <div className="space-y-2">
          {notifs.map(n => (
            <div key={n._id}
              className={`bg-white border rounded-xl p-4 transition-colors ${
                n.read ? 'border-[#e8e8e3]' : 'border-[#ccc] bg-[#fafaf8]'
              }`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={`text-sm truncate ${n.read ? 'text-gray-500' : 'text-[#333] font-medium'}`}>
                    {n.title}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">{n.message}</p>
                  {n.company_name && (
                    <p className="text-[10px] text-gray-300 mt-0.5">关联企业：{n.company_name}</p>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <p className="text-[10px] text-gray-300 whitespace-nowrap">
                    {n.created_at?.slice(0, 16).replace('T', ' ')}
                  </p>
                  {!n.read && (
                    <span className="text-[10px] text-[#333]">● 未读</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
