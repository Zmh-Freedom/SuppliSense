import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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
    onError: () => alert('上传失败，请重试'),
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
      alert('知识库已清空');
    },
    onError: () => alert('清空失败，请重试'),
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
    if (!confirm('确定要清空知识库吗？此操作不可恢复。')) return;
    clearMutation.mutate();
  };

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto px-4 py-6">
      <h2 className="text-lg font-semibold mb-4">知识库管理</h2>

      {/* Stats */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 mb-4">
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
              <div className="text-2xl font-bold text-[#333]">{stats.total_documents}</div>
              <div className="text-gray-400 text-xs">文档分块</div>
            </div>
            <div>
              <div className="text-sm font-mono text-[#333] truncate">{stats.collection_name}</div>
              <div className="text-gray-400 text-xs">集合名称</div>
            </div>
            <div>
              <div className="text-sm font-mono text-[#333] truncate">{stats.embedding_model}</div>
              <div className="text-gray-400 text-xs">向量模型</div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400">点击刷新加载统计信息</p>
        )}
      </div>

      {/* Upload */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 mb-4">
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
            className="flex-1 text-sm text-gray-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:bg-[#f0f0eb] file:text-[#333] hover:file:bg-[#e8e8e3] file:cursor-pointer disabled:opacity-50"
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
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 mb-4">
        <h3 className="text-sm font-medium mb-2">检索测试</h3>
        <div className="flex items-center gap-2 mb-3">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="输入查询关键词..."
            className="flex-1 text-sm border border-[#e8e8e3] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[#bbb] placeholder-gray-300"
          />
          <button
            onClick={handleSearch}
            disabled={searchMutation.isPending || !searchQuery.trim()}
            className="text-sm bg-[#333] text-white rounded-lg px-4 py-1.5 hover:bg-[#555] disabled:opacity-40"
          >
            {searchMutation.isPending ? '检索中' : '检索'}
          </button>
        </div>
        {searchResults.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-auto">
            {searchResults.map((result, i) => (
              <div key={result.id || i} className="p-3 bg-[#f9f9f5] rounded-lg text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-400">
                    {String(result.metadata?.source || 'Unknown')}
                    {result.metadata?.page ? ` (第${String(result.metadata.page)}页)` : ''}
                  </span>
                  <span className="text-xs text-gray-400">
                    相似度: {((1 - (result.distance || 0)) * 100).toFixed(1)}%
                  </span>
                </div>
                <p className="text-[#333] text-xs leading-relaxed line-clamp-3">
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
      <div className="mt-auto pt-4 border-t border-[#e8e8e3]">
        <button
          onClick={handleClear}
          disabled={clearMutation.isPending}
          className="text-sm text-red-500 hover:text-red-600 disabled:opacity-50"
        >
          {clearMutation.isPending ? '清空中…' : '清空知识库'}
        </button>
      </div>
    </div>
  );
}
