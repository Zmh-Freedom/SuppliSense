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
  if (score >= 60) return '#dc2626';
  if (score >= 30) return '#d97706';
  return '#16a34a';
}

const CENTER_X = 400;
const CENTER_Y = 300;
const RADIUS_BASE = 180;

function layoutNodes(gnodes: GraphNode[]): Node[] {
  const center = gnodes.find(n => n.type === 'center');
  const others = gnodes.filter(n => n.type !== 'center');

  const result: Node[] = [];

  // Center node
  if (center) {
    result.push({
      id: center.id,
      position: { x: CENTER_X, y: CENTER_Y },
      data: { label: center.label },
      draggable: false,
      style: {
        background: '#333',
        color: '#fff',
        border: '2px solid #333',
        borderRadius: '12px',
        padding: '12px 20px',
        fontSize: '13px',
        fontWeight: 600,
        width: 140,
        textAlign: 'center' as const,
      },
    });
  }

  // Arrange related nodes in a circle
  // Group by relation: branches first (inner), then supply chain, then same industry (outer)
  const branches = others.filter(n => n.type === 'branch');
  const supply = others.filter(n => n.type === 'supply_chain');
  const industry = others.filter(n => n.type === 'same_industry');

  const groups = [
    { items: branches, radius: RADIUS_BASE },
    { items: supply, radius: RADIUS_BASE + 80 },
    { items: industry, radius: RADIUS_BASE + 160 },
  ];

  for (const group of groups) {
    const n = group.items.length;
    for (let i = 0; i < n; i++) {
      const gn = group.items[i];
      const angle = (2 * Math.PI * i) / n - Math.PI / 2; // Start from top
      result.push({
        id: gn.id,
        position: {
          x: CENTER_X + group.radius * Math.cos(angle),
          y: CENTER_Y + group.radius * Math.sin(angle),
        },
        data: { label: gn.label },
        draggable: false,
        style: {
          background: '#fff',
          color: '#333',
          border: `2px solid ${riskColor(gn.risk_score)}`,
          borderRadius: '8px',
          padding: '8px 14px',
          fontSize: '11px',
          maxWidth: 130,
          textAlign: 'center' as const,
        },
      });
    }
  }

  return result;
}

function buildEdges(gedges: GraphEdge[]): Edge[] {
  return gedges.map(ge => ({
    id: ge.id,
    source: ge.source,
    target: ge.target,
    label: ge.label,
    type: 'smoothstep',
    animated: false,
    style: { stroke: '#bbb', strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: '#bbb', width: 16, height: 16 },
  }));
}

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
      {/* left */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">风险传染图谱</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家企业</p>
        </div>
        <div className="py-1">
          {companies.map(c => (
            <button key={c.company_name} onClick={() => select(c.company_name)}
              className={`w-full text-left px-4 py-2.5 text-sm flex items-center justify-between ${
                selected === c.company_name ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'
              }`}>
              <span className="truncate">{c.company_name.slice(0, 14)}</span>
              <span className="text-[11px] text-gray-400 ml-2 shrink-0">
                {c.related_count}关联{c.high_risk_related_count > 0 && <span className="text-red-400"> {c.high_risk_related_count}⚠️</span>}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* right: graph */}
      <div className="flex-1 bg-[#f5f5f3]">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">选择企业查看风险传染图谱</div>
        ) : loading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">加载中…</div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            attributionPosition="bottom-left"
          >
            <Background color="#e0e0e0" />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={(n) => riskColor((n as unknown as { risk_score?: number }).risk_score ?? 0)}
              style={{ border: '1px solid #e8e8e3', borderRadius: '8px' }}
            />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
