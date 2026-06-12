import { useState, useRef } from 'react';
import { api } from '../api';

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
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadStats = async () => {
    try {
      const res = await api.get<DocumentStats>('/knowledge/stats');
      setStats(res);
    } catch {
      setStats(null);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadResult(null);

    try {
      const res = await api.upload<UploadResult>('/knowledge/upload', file);
      setUploadResult(res);
      loadStats();
    } catch (err) {
      console.error('Upload failed:', err);
      alert('上传失败，请重试');
    }

    setUploading(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;

    setSearching(true);
    try {
      const res = await api.post<{ results: SearchResult[] }>('/knowledge/search', {
        query: searchQuery,
        n_results: 5,
      });
      setSearchResults(res.results || []);
    } catch {
      setSearchResults([]);
    }
    setSearching(false);
  };

  const handleClear = async () => {
    if (!confirm('确定要清空知识库吗？此操作不可恢复。')) return;

    try {
      await api.delete('/knowledge/clear');
      setStats(null);
      setSearchResults([]);
      alert('知识库已清空');
    } catch {
      alert('清空失败，请重试');
    }
  };

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto px-4 py-6">
      <h2 className="text-lg font-semibold mb-4">知识库管理</h2>

      {/* Stats */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">知识库统计</span>
          <button
            onClick={loadStats}
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
            disabled={uploading}
            className="flex-1 text-sm text-gray-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:bg-[#f0f0eb] file:text-[#333] hover:file:bg-[#e8e8e3] file:cursor-pointer disabled:opacity-50"
          />
          {uploading && <span className="text-xs text-gray-400">上传中...</span>}
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
            disabled={searching || !searchQuery.trim()}
            className="text-sm bg-[#333] text-white rounded-lg px-4 py-1.5 hover:bg-[#555] disabled:opacity-40"
          >
            {searching ? '检索中' : '检索'}
          </button>
        </div>
        {searchResults.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-auto">
            {searchResults.map((result, i) => (
              <div key={result.id || i} className="p-3 bg-[#f9f9f5] rounded-lg text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-400">
                    {String(result.metadata?.source || 'Unknown')}
                    {result.metadata?.page && ` (第${result.metadata.page}页)`}
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
        {searchResults.length === 0 && searchQuery && !searching && (
          <p className="text-sm text-gray-400 text-center py-4">未找到相关文档</p>
        )}
      </div>

      {/* Clear */}
      <div className="mt-auto pt-4 border-t border-[#e8e8e3]">
        <button
          onClick={handleClear}
          className="text-sm text-red-500 hover:text-red-600"
        >
          清空知识库
        </button>
      </div>
    </div>
  );
}
