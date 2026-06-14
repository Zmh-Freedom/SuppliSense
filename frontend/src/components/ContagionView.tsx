import { useState, useEffect, useCallback } from 'react';
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
  getBezierPath,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api } from '../api';

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

function riskColor(score: number): string {
  if (score >= 60) return '#ef4444';
  if (score >= 30) return '#f59e0b';
  return '#22c55e';
}

function riskBg(score: number): string {
  if (score >= 60) return '#fef2f2';
  if (score >= 30) return '#fffbeb';
  return '#f0fdf4';
}

function relationIcon(rel: string): string {
  switch (rel) {
    case 'branch': return '🏢';
    case 'supply_chain': return '🔗';
    case 'same_industry': return '🏭';
    default: return '●';
  }
}

function edgeColor(rel: string): string {
  switch (rel) {
    case 'branch': return '#6366f1';
    case 'supply_chain': return '#f59e0b';
    case 'same_industry': return '#10b981';
    default: return '#94a3b8';
  }
}

// ---- Custom edge with styled label ----
function StyledEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition,
  data, markerEnd,
}: any) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={{ stroke: data?.color || '#bbb', strokeWidth: 2 }} markerEnd={markerEnd} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            background: 'white',
            padding: '2px 8px',
            borderRadius: '10px',
            fontSize: '11px',
            fontWeight: 500,
            color: '#555',
            border: '1px solid #e5e5e0',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
          }}
        >
          {data?.label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const edgeTypes = { styled: StyledEdge };

// ---- Layout ----
const CENTER_X = 520;
const CENTER_Y = 360;

function layoutNodes(gnodes: GraphNode[]): Node[] {
  const center = gnodes.find(n => n.type === 'center');
  const others = gnodes.filter(n => n.type !== 'center');

  const branches = others.filter(n => n.type === 'branch');
  const supply = others.filter(n => n.type === 'supply_chain');
  const industry = others.filter(n => n.type === 'same_industry');

  // Dynamic radius based on node count per group
  const baseR = 220;
  const perNode = 35;
  const groups = [
    { items: branches, radius: baseR + branches.length * perNode, color: edgeColor('branch') },
    { items: supply, radius: baseR + 100 + supply.length * perNode, color: edgeColor('supply_chain') },
    { items: industry, radius: baseR + 200 + industry.length * perNode, color: edgeColor('same_industry') },
  ];

  const allNodes: Node[] = [];

  if (center) {
    allNodes.push({
      id: center.id,
      position: { x: CENTER_X, y: CENTER_Y },
      data: { label: center.label },
      draggable: false,
      style: {
        background: 'linear-gradient(135deg, #1e293b 0%, #334155 100%)',
        color: '#fff',
        border: '3px solid #475569',
        borderRadius: '16px',
        padding: '14px 24px',
        fontSize: '14px',
        fontWeight: 600,
        boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
        textAlign: 'center' as const,
      },
    });
  }

  for (const group of groups) {
    const n = group.items.length;
    if (n === 0) continue;
    // If only 1 item, place it directly below center
    if (n === 1) {
      const gn = group.items[0];
      allNodes.push({
        id: gn.id,
        position: { x: CENTER_X, y: CENTER_Y + group.radius },
        data: { label: gn.label, riskIcon: relationIcon(gn.type) },
        draggable: false,
        style: {
          background: riskBg(gn.risk_score),
          color: '#1e293b',
          border: `2px solid ${riskColor(gn.risk_score)}`,
          borderRadius: '10px',
          padding: '10px 16px',
          fontSize: '12px',
          fontWeight: 500,
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
          textAlign: 'center' as const,
        },
      });
      continue;
    }

    for (let i = 0; i < n; i++) {
      const gn = group.items[i];
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      allNodes.push({
        id: gn.id,
        position: {
          x: CENTER_X + group.radius * Math.cos(angle),
          y: CENTER_Y + group.radius * Math.sin(angle),
        },
        data: { label: gn.label, riskIcon: relationIcon(gn.type) },
        draggable: false,
        style: {
          background: riskBg(gn.risk_score),
          color: '#1e293b',
          border: `2px solid ${riskColor(gn.risk_score)}`,
          borderRadius: '10px',
          padding: '10px 16px',
          fontSize: '12px',
          fontWeight: 500,
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
          textAlign: 'center' as const,
        },
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
    data: { label: ge.label, color: edgeColor(ge.relation) },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edgeColor(ge.relation),
      width: 18,
      height: 18,
    },
  }));
}

// ---- Legend ----
function Legend() {
  const items = [
    { color: edgeColor('branch'), icon: '🏢', label: '分支机构 / 子公司' },
    { color: edgeColor('supply_chain'), icon: '🔗', label: '供应链依赖' },
    { color: edgeColor('same_industry'), icon: '🏭', label: '同行业关联' },
  ];

  return (
    <div className="absolute bottom-4 left-4 z-10 bg-white/90 backdrop-blur rounded-xl border border-[#e8e8e3] px-4 py-3 text-xs shadow-sm">
      <div className="font-medium text-[#555] mb-2">图例</div>
      {items.map(item => (
        <div key={item.label} className="flex items-center gap-2 py-0.5">
          <span style={{ color: item.color, fontSize: '16px' }}>●</span>
          <span className="text-gray-500">{item.icon} {item.label}</span>
        </div>
      ))}
      <div className="mt-2 pt-2 border-t border-[#e8e8e3] text-[11px] text-gray-400">
        🟢低风险 <span className="mx-1">🟡中风险</span> <span className="ml-1">🔴高风险</span>
      </div>
    </div>
  );
}

// ---- Component ----
export default function ContagionView() {
  const [companies, setCompanies] = useState<ContagionSummary[]>([]);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    const controller = new AbortController();
    api.get<{ companies: ContagionSummary[] }>('/p2/contagion', undefined, controller.signal)
      .then(d => setCompanies(d.companies || []))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  const select = useCallback(async (name: string) => {
    setSelected(name);
    setLoading(true);
    try {
      const g = await api.get<GraphData>(`/p2/contagion/${encodeURIComponent(name)}/graph`);
      setNodes(layoutNodes(g.nodes));
      setEdges(buildEdges(g.edges));
    } catch {
      setNodes([]);
      setEdges([]);
    }
    setLoading(false);
  }, [setNodes, setEdges]);

  return (
    <div className="flex h-full">
      {/* left panel */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">风险传染图谱</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家监控企业</p>
        </div>
        <div className="py-1">
          {companies.map(c => (
            <button key={c.company_name} onClick={() => select(c.company_name)}
              className={`w-full text-left px-4 py-2.5 text-sm flex items-center justify-between transition-colors ${
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
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            fitView
            fitViewOptions={{ padding: 0.4 }}
            attributionPosition="bottom-left"
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#e2e8f0" gap={20} size={1} />
            <Controls showInteractive={false} className="bg-white/80 border-[#e8e8e3]" />
            <MiniMap
              nodeColor={(n) => riskColor((n as unknown as { risk_score?: number }).risk_score ?? 0)}
              maskColor="rgba(248,250,252,0.6)"
              style={{ border: '1px solid #e8e8e3', borderRadius: '10px', background: '#f8fafc' }}
            />
            <Legend />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
