import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { api } from '../api';
import { queryKeys } from '../query-keys';

interface DocumentStats {
  total_documents: number;
  collection_name: string;
  embedding_model: string;
}

interface UploadResult {
  filename: string;
  chunks_added: number;
  doc_ids: string[];
}

interface SearchResult {
  content: string;
  metadata: Record<string, unknown>;
  distance: number;
  id: string;
}

export default function KnowledgePanel() {
  const queryClient = useQueryClient();
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [message, setMessage] = useState<{type: 'success' | 'error'; text: string} | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const statsQuery = useQuery({
    queryKey: queryKeys.knowledgeStats,
    queryFn: () => api.get<DocumentStats>('/knowledge/stats'),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.upload<UploadResult>('/knowledge/upload', file),
    onSuccess: (res) => {
      setUploadResult(res);
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeStats });
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
    onError: () => setMessage({type: 'error', text: '上传失败，请重试'}),
  });

  const searchMutation = useMutation({
    mutationFn: () => api.post<{ results: SearchResult[] }>('/knowledge/search', {
      query: searchQuery,
      n_results: 5,
    }),
    onSuccess: (res) => setSearchResults(res.results || []),
    onError: () => setSearchResults([]),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.delete('/knowledge/clear'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeStats });
      setSearchResults([]);
      setMessage({type: 'success', text: '知识库已清空'});
      setConfirmClear(false);
    },
    onError: () => { setMessage({type: 'error', text: '清空失败，请重试'}); setConfirmClear(false); },
  });

  const stats = statsQuery.data ?? null;

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadResult(null);
    uploadMutation.mutate(file);
  };

  const handleSearch = () => {
    if (!searchQuery.trim()) return;
    searchMutation.mutate();
  };

  const handleClear = () => {
    setConfirmClear(true);
  };

  const confirmClearAction = () => {
    clearMutation.mutate();
  };

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto px-4 py-6">
      <h2 className="text-lg font-semibold mb-4">知识库管理</h2>

      <AnimatePresence>
        {message && (
          <motion.div
            className={`mb-4 rounded-lg px-4 py-2 text-sm ${message.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-600 border border-red-200'}`}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
          >
            {message.text}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Stats */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 mb-4 shadow-sm">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">知识库统计</span>
          <button
            onClick={() => statsQuery.refetch()}
            className="text-xs text-gray-400 hover:text-gray-600"
          >
            刷新
          </button>
        </div>
        {stats ? (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <div className="text-2xl font-bold text-[var(--color-text)]">{stats.total_documents}</div>
              <div className="text-gray-400 text-xs">文档分块</div>
            </div>
            <div>
              <div className="text-sm font-mono text-[var(--color-text)] truncate">{stats.collection_name}</div>
              <div className="text-gray-400 text-xs">集合名称</div>
            </div>
            <div>
              <div className="text-sm font-mono text-[var(--color-text)] truncate">{stats.embedding_model}</div>
              <div className="text-gray-400 text-xs">向量模型</div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400">点击刷新加载统计信息</p>
        )}
      </div>

      {/* Upload */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 mb-4 shadow-sm">
        <h3 className="text-sm font-medium mb-2">上传文档</h3>
        <p className="text-xs text-gray-400 mb-3">
          支持格式：PDF、Word、Excel、TXT。文档将被分块并存入向量数据库。
        </p>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.xlsx,.txt"
            onChange={handleUpload}
            disabled={uploadMutation.isPending}
            className="flex-1 text-sm text-gray-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:bg-[var(--color-code-bg)] file:text-[var(--color-text)] hover:file:bg-[var(--color-surface-selected)] file:cursor-pointer disabled:opacity-50"
          />
          {uploadMutation.isPending && <span className="text-xs text-gray-400">上传中...</span>}
        </div>
        {uploadResult && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg text-sm">
            <p className="text-green-700 font-medium">✓ 上传成功</p>
            <p className="text-green-600 text-xs mt-1">
              {uploadResult.filename}：提取 {uploadResult.chunks_added} 个文本块
            </p>
          </div>
        )}
      </div>

      {/* Search */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 mb-4 shadow-sm">
        <h3 className="text-sm font-medium mb-2">检索测试</h3>
        <div className="flex items-center gap-2 mb-3">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="输入查询关键词..."
            className="flex-1 text-sm border border-[var(--color-border)] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-border-focus)] placeholder-gray-300"
          />
          <button
            onClick={handleSearch}
            disabled={searchMutation.isPending || !searchQuery.trim()}
            className="text-sm bg-[var(--color-primary-bg)] text-white rounded-lg px-4 py-1.5 hover:bg-[var(--color-primary-hover)] disabled:opacity-40"
          >
            {searchMutation.isPending ? '检索中' : '检索'}
          </button>
        </div>
        {searchResults.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-auto">
            {searchResults.map((result, i) => (
              <div key={result.id || i} className="p-3 bg-[var(--color-surface-hover)] rounded-lg text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-400">
                    {String(result.metadata?.source || 'Unknown')}
                    {result.metadata?.page ? ` (第${String(result.metadata.page)}页)` : ''}
                  </span>
                  <span className="text-xs text-gray-400">
                    相似度: {((1 - (result.distance || 0)) * 100).toFixed(1)}%
                  </span>
                </div>
                <p className="text-[var(--color-text)] text-xs leading-relaxed line-clamp-3">
                  {result.content}
                </p>
              </div>
            ))}
          </div>
        )}
        {searchResults.length === 0 && searchQuery && !searchMutation.isPending && (
          <p className="text-sm text-gray-400 text-center py-4">未找到相关文档</p>
        )}
      </div>

      {/* Clear */}
      <div className="mt-auto pt-4 border-t border-[var(--color-border)]">
        <AnimatePresence mode="wait">
          {confirmClear ? (
            <motion.div
              key="confirm"
              className="flex items-center gap-3"
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 8 }}
              transition={{ duration: 0.18 }}
            >
              <span className="text-sm text-red-600">确定要清空知识库吗？此操作不可恢复。</span>
              <button
                onClick={confirmClearAction}
                disabled={clearMutation.isPending}
                className="text-sm bg-red-500 text-white rounded-lg px-3 py-1.5 hover:bg-red-600 disabled:opacity-50"
              >
                {clearMutation.isPending ? '清空中…' : '确定'}
              </button>
              <button
                onClick={() => setConfirmClear(false)}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                取消
              </button>
            </motion.div>
          ) : (
            <motion.button
              key="clear-btn"
              onClick={handleClear}
              disabled={clearMutation.isPending}
              className="text-sm text-red-500 hover:text-red-600 disabled:opacity-50"
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.18 }}
            >
              清空知识库
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
