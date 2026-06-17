import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ReactFlow,
  Controls,
  Background,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  MarkerType,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api } from '../api';
import { queryKeys } from '../query-keys';

interface GraphNode {
  id: string;
  label: string;
  type: string;
  risk_score: number;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  label: string;
}

interface GraphData {
  company_name: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface ContagionSummary {
  company_name: string;
  related_count: number;
  high_risk_related_count: number;
}

// ---- Colors ----
const COLORS: Record<string, { line: string; bg: string; text: string; label: string }> = {
  branch: { line: '#818cf8', bg: '#eef2ff', text: '#4338ca', label: '分支' },
  supply_chain: { line: '#fbbf24', bg: '#fffbeb', text: '#b45309', label: '供应链' },
  same_industry: { line: '#34d399', bg: '#ecfdf5', text: '#047857', label: '同行业' },
};

function riskColor(score: number) {
  if (score >= 60) return { line: '#f87171', bg: '#fef2f2', text: '#dc2626', label: '高风险' };
  if (score >= 30) return { line: '#fbbf24', bg: '#fffbeb', text: '#b45309', label: '中风险' };
  return { line: '#4ade80', bg: '#f0fdf4', text: '#16a34a', label: '低风险' };
}

interface NodeData {
  label: string;
  risk_score?: number;
  relationType?: string;
  [key: string]: unknown;
}

// ---- Custom Center Node ----
function CenterNode({ data }: { data: NodeData }) {
  return (
    <div className="relative">
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #334155 100%)',
        color: '#f1f5f9',
        border: '2px solid #475569',
        borderRadius: 20,
        padding: '16px 28px',
        fontSize: 15,
        fontWeight: 700,
        boxShadow: '0 8px 32px rgba(15,23,42,0.25), 0 0 0 4px rgba(100,116,139,0.1)',
        textAlign: 'center',
        minWidth: 160,
        letterSpacing: '0.02em',
      }}>
        <div className="text-[10px] font-normal text-slate-400 mb-0.5 tracking-wider uppercase">核心企业</div>
        {data.label}
      </div>
    </div>
  );
}

// ---- Custom Related Node ----
function RelatedNode({ data }: { data: NodeData }) {
  const rc = riskColor(data.risk_score || 0);
  const rel = COLORS[data.relationType || ''] || COLORS.branch;

  return (
    <div
      style={{
        background: rc.bg,
        borderRadius: 14,
        border: `2px solid ${rc.line}`,
        padding: '10px 16px',
        minWidth: 120,
        maxWidth: 160,
        boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
        textAlign: 'center',
        transition: 'box-shadow 0.2s',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = `0 4px 20px ${rc.line}40`; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)'; }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{
        fontSize: 10,
        fontWeight: 600,
        color: rel.text,
        background: rel.bg,
        borderRadius: 6,
        padding: '1px 8px',
        display: 'inline-block',
        marginBottom: 4,
        letterSpacing: '0.05em',
      }}>
        {rel.label}
      </div>
      <div style={{
        fontSize: 12,
        fontWeight: 500,
        color: '#1e293b',
        lineHeight: 1.4,
        wordBreak: 'break-all',
      }}>
        {data.label}
      </div>
      {data.risk_score != null && data.risk_score > 0 && (
        <div style={{
          marginTop: 6,
          fontSize: 10,
          fontWeight: 600,
          color: rc.text,
          background: '#fff',
          borderRadius: 8,
          padding: '1px 8px',
          display: 'inline-block',
          border: `1px solid ${rc.line}40`,
        }}>
          {rc.label} · {data.risk_score}分
        </div>
      )}
    </div>
  );
}

const nodeTypes = { center: CenterNode, related: RelatedNode };

// ---- Custom Edge ----
interface EdgeData {
  color?: string;
  dashed?: boolean;
  label?: string;
  [key: string]: unknown;
}

interface StyledEdgeProps {
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourcePosition: Position;
  targetPosition: Position;
  data?: EdgeData;
  markerEnd?: string;
}

function StyledEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }: StyledEdgeProps) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition, borderRadius: 12 });

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={{ stroke: data?.color || '#cbd5e1', strokeWidth: 2, strokeDasharray: data?.dashed ? '6 4' : 'none' }} markerEnd={markerEnd} />
      <EdgeLabelRenderer>
        <div style={{
          position: 'absolute',
          transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
          background: '#fff',
          padding: '2px 10px',
          borderRadius: 12,
          fontSize: 11,
          fontWeight: 500,
          color: data?.color || '#64748b',
          border: `1px solid ${data?.color || '#e2e8f0'}40`,
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
          boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
        }}>
          {data?.label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const edgeTypes = { styled: StyledEdge };

// ---- Layout ----
function layoutNodes(gnodes: GraphNode[], viewW: number, viewH: number): Node[] {
  const cx = viewW / 2;
  const cy = viewH / 2;
  const center = gnodes.find(n => n.type === 'center');
  const others = gnodes.filter(n => n.type !== 'center');

  const branches = others.filter(n => n.type === 'branch');
  const supply = others.filter(n => n.type === 'supply_chain');
  const industry = others.filter(n => n.type === 'same_industry');

  const gap = 120;
  const groups = [
    { items: branches, radius: Math.max(200, gap + branches.length * 40) },
    { items: supply, radius: Math.max(300, gap + 100 + supply.length * 40) },
    { items: industry, radius: Math.max(400, gap + 200 + industry.length * 40) },
  ];

  const allNodes: Node[] = [];

  if (center) {
    allNodes.push({
      id: center.id,
      type: 'center',
      position: { x: cx, y: cy },
      data: { label: center.label },
      draggable: false,
    });
  }

  for (const group of groups) {
    const n = group.items.length;
    if (n === 0) continue;
    for (let i = 0; i < n; i++) {
      const gn = group.items[i];
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      allNodes.push({
        id: gn.id,
        type: 'related',
        position: {
          x: cx + group.radius * Math.cos(angle),
          y: cy + group.radius * Math.sin(angle),
        },
        data: {
          label: gn.label,
          risk_score: gn.risk_score,
          relationType: gn.type,
        },
        draggable: false,
      });
    }
  }

  return allNodes;
}

function buildEdges(gedges: GraphEdge[]): Edge[] {
  return gedges.map(ge => ({
    id: ge.id,
    source: ge.source,
    target: ge.target,
    type: 'styled',
    data: {
      label: ge.label,
      color: COLORS[ge.relation]?.line || '#94a3b8',
      dashed: ge.relation === 'same_industry',
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: COLORS[ge.relation]?.line || '#94a3b8',
      width: 16,
      height: 16,
    },
  }));
}

// ---- Legend ----
function Legend() {
  return (
    <div className="absolute bottom-4 left-4 z-10 bg-white/90 backdrop-blur-sm rounded-2xl border border-[#e8e8e3] px-5 py-4 text-xs shadow-lg">
      <div className="font-semibold text-[#555] mb-3">图例</div>
      {Object.entries(COLORS).map(([key, c]) => (
        <div key={key} className="flex items-center gap-2.5 py-1">
          <span className="w-3 h-3 rounded-full shrink-0" style={{ background: c.line }} />
          <span className="text-gray-500 text-[11px]">{c.label}{key === 'same_industry' ? '（虚线）' : ''}</span>
        </div>
      ))}
      <div className="mt-3 pt-3 border-t border-[#e8e8e3] flex items-center gap-4">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-400" /><span className="text-[11px] text-gray-400">低风险</span></span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-amber-400" /><span className="text-[11px] text-gray-400">中风险</span></span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-400" /><span className="text-[11px] text-gray-400">高风险</span></span>
      </div>
    </div>
  );
}

// ---- Component ----
export default function ContagionView() {
  const [selected, setSelected] = useState<string>(() => {
    try { return localStorage.getItem('contagion_company') || ''; } catch { return ''; }
  });
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const listQuery = useQuery({
    queryKey: queryKeys.contagionSummary,
    queryFn: () => api.get<{ companies: ContagionSummary[] }>('/p2/contagion'),
  });

  const graphQuery = useQuery({
    queryKey: queryKeys.contagionGraph(selected),
    queryFn: () => api.get<GraphData>(`/p2/contagion/${encodeURIComponent(selected)}/graph`),
    enabled: !!selected,
  });

  const companies = listQuery.data?.companies ?? [];
  const loading = graphQuery.isLoading && !!selected;

  // Sync graph data to ReactFlow state
  useEffect(() => {
    if (graphQuery.data) {
      setNodes(layoutNodes(graphQuery.data.nodes, 900, 650));
      setEdges(buildEdges(graphQuery.data.edges));
    } else if (graphQuery.isError) {
      setNodes([]);
      setEdges([]);
    }
  }, [graphQuery.data, graphQuery.isError, setNodes, setEdges]);

  const select = (name: string) => {
    setSelected(name);
    localStorage.setItem('contagion_company', name);
  };

  return (
    <div className="flex flex-col md:flex-row h-full">
      {/* left panel */}
      <div className="w-full md:w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0 max-h-48 md:max-h-none">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">风险传染图谱</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家监控企业</p>
        </div>
        <div className="py-1">
          {companies.map(c => (
            <button key={c.company_name} onClick={() => select(c.company_name)} disabled={loading}
              className={`w-full text-left px-4 py-2.5 text-sm flex items-center justify-between transition-colors disabled:opacity-50 ${
                selected === c.company_name ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'
              }`}>
              <span className="truncate">{c.company_name.slice(0, 16)}</span>
              <span className="text-[11px] text-gray-400 ml-2 shrink-0">
                {c.related_count}关联{c.high_risk_related_count > 0 && <span className="text-red-400 ml-0.5">{c.high_risk_related_count}⚠</span>}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* right: graph */}
      <div className="flex-1 bg-[#f8fafc] relative">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">选择企业查看风险传染图谱</div>
        ) : loading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">加载中…</div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            fitView
            fitViewOptions={{ padding: 0.5 }}
            attributionPosition="bottom-left"
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#e2e8f0" gap={24} size={1} />
            <Controls showInteractive={false} className="bg-white/80 border-[#e8e8e3] rounded-xl shadow-sm" />
            <MiniMap
              nodeColor={(n) => riskColor((n as unknown as { risk_score?: number }).risk_score ?? 0).line}
              maskColor="rgba(248,250,252,0.6)"
              style={{ border: '1px solid #e8e8e3', borderRadius: 12, background: '#f8fafc' }}
            />
            <Legend />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
