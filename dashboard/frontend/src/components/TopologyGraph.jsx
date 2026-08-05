import React, { useEffect, useMemo, useCallback, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  useNodesState,
  useEdgesState,
  Panel,
  Handle,
  Position
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { Badge, Card } from './Common.jsx';

function overlayState(hypotheses = []) {
  const primary = hypotheses?.[0] || null;
  if (!primary) return null;
  const roots = new Set(primary.root_set || []);
  const residuals = new Set(primary.unexplained_residuals || []);
  const paths = primary.causal_paths || []
  const contradictions = new Set(
    [
      ...(primary.refute_evidence_refs || primary.contradicting_refs || []),
      ...paths.flatMap(path => path?.contradicting_refs || []),
    ]
      .map(ref => String(ref).split(':').at(-1)),
  );
  const pathNodes = new Set();
  const pathEdges = new Map();
  for (const path of paths) {
    const nodes = path.nodes || path.node_ids || (Array.isArray(path) ? path : []);
    nodes.forEach(node => pathNodes.add(node));
    ;(path.edge_ids || []).forEach(id => pathEdges.set(id, path))
    for (let index = 0; index < nodes.length - 1; index += 1) {
      pathEdges.set(`${nodes[index + 1]}→${nodes[index]}`, path)
    }
  }
  return { primary, roots, residuals, contradictions, pathNodes, pathEdges };
}

function nodeKind(node) {
  return node.node_type || node.kind || 'unknown';
}

function nodeLabel(node) {
  return node.label || node.name || node.service || node.id;
}

function nodeSubtitle(node) {
  if (node.operation) return node.operation;
  if (node.namespace) return node.namespace;
  if (node.service_name && node.service_name !== node.id) return node.service_name;
  if (node.host_node) return `host: ${node.host_node}`;
  return null;
}

function nodeHoverText(node) {
  const lines = []
  if (node.condition) lines.push(`Condition: ${node.condition}`)
  if (node.status_detail) lines.push(node.status_detail)
  else if (node.health_reason) lines.push(String(node.health_reason).replaceAll('_', ' '))
  if (node.namespace) lines.push(`Namespace: ${node.namespace}`)
  if (node.restart_count != null && Number(node.restart_count) > 0) {
    lines.push(`Restarts: ${node.restart_count}`)
  }
  return lines.join('\n')
}

function nodeTone(node) {
  if (node.status === 'origin' || node.role === 'origin') return 'purple'
  if (node.status === 'amplifier' || node.role === 'amplifier') return 'yellow'
  if (node.status === 'blast') return 'orange'
  if (node.status === 'error') return 'red'
  if (node.status === 'warn') return 'yellow'
  if (node.status === 'infra') return 'blue'
  if (node.status === 'unknown') return 'grey'
  const kind = nodeKind(node);
  if (kind === 'node' || kind === 'infra') {
    if (node.condition && !['Ready', 'Running'].includes(node.condition)) return 'red';
    if ((node.cpu_usage_pct || node.cpu_pct || 0) >= 90) return 'yellow';
    return 'grey';
  }
  if (kind === 'pod') {
    if ((node.restart_count || 0) >= 3) return 'red';
    if ((node.restart_count || 0) > 0) return 'yellow';
    return 'green';
  }
  if (kind === 'span') {
    return Number(node.status_code) === 2 ? 'red' : 'green';
  }
  return 'green';
}

function edgeStroke(edge) {
  if (edge.edge_type === 'infra_to_service') return 'rgba(255, 100, 110, 0.75)';
  if (edge.edge_type === 'node_to_pod') return 'rgba(88, 201, 255, 0.6)';
  if (edge.edge_type === 'pod_to_service') return 'rgba(39, 212, 160, 0.6)';
  if (edge.edge_type === 'trace_call') return 'rgba(248, 184, 74, 0.65)';
  if ((edge.error_rate || 0) >= 0.05) return 'rgba(255, 100, 110, 0.85)';
  return 'rgba(142, 164, 188, 0.46)';
}

function edgeLabel(edge) {
  if ((edge.request_rate || 0) > 0) return `${edge.request_rate.toFixed(1)} r/s`;
  if ((edge.latency_p99_ms || 0) > 0) return `${edge.latency_p99_ms.toFixed(0)} ms`;
  return edge.edge_type?.replaceAll('_', ' ') || '';
}

const getToneStyles = (tone, selected, overlay) => {
  const base = {
    padding: '12px 14px',
    borderRadius: '12px',
    border: `2px solid ${selected ? '#ffffff' : 'transparent'}`,
    minWidth: '160px',
    boxShadow: selected ? '0 0 0 4px rgba(255,255,255,0.1)' : '0 4px 12px rgba(0,0,0,0.4)',
    color: '#fff',
    fontFamily: 'Sora, sans-serif'
  };
  let result
  if (tone === 'red') result = { ...base, background: 'rgba(255, 80, 95, 0.85)', borderColor: selected ? '#ffffff' : '#ff7881' }
  else if (tone === 'yellow') result = { ...base, background: 'rgba(248, 170, 45, 0.85)', borderColor: selected ? '#ffffff' : '#f8b84a' }
  else if (tone === 'green') result = { ...base, background: 'rgba(30, 180, 130, 0.85)', borderColor: selected ? '#ffffff' : '#27d4a0' }
  else if (tone === 'purple') result = { ...base, background: 'rgba(140, 90, 220, 0.9)', borderColor: selected ? '#ffffff' : '#c79cff' }
  else if (tone === 'orange') result = { ...base, background: 'rgba(255, 140, 70, 0.85)', borderColor: selected ? '#ffffff' : '#ff9d66' }
  else if (tone === 'blue') result = { ...base, background: 'rgba(40, 115, 180, 0.88)', borderColor: selected ? '#ffffff' : '#55b7ff' }
  else result = { ...base, background: 'rgba(88, 101, 118, 0.9)', borderColor: selected ? '#ffffff' : '#9aa9ba' }
  if (overlay?.root) return { ...result, borderColor: '#c79cff', boxShadow: '0 0 0 4px rgba(199,156,255,0.35)' }
  if (overlay?.contradiction) return { ...result, borderColor: '#ff9d66', borderStyle: 'dashed' }
  if (overlay?.residual) return { ...result, borderColor: '#f8b84a', borderStyle: 'dotted' }
  if (overlay?.path) return { ...result, borderColor: '#61dafb' }
  return result
};

const CustomNode = ({ data, selected }) => {
  const tone = nodeTone(data.raw);
  const styles = getToneStyles(tone, selected, data.overlay);
  const subtitle = nodeSubtitle(data.raw);
  const hoverText = nodeHoverText(data.raw);

  return (
    <div className="topology-node" style={{ ...styles, position: 'relative' }} title={hoverText || undefined}>
      <Handle type="target" position={Position.Top} style={{ background: '#fff', width: 8, height: 8 }} />
      <div style={{ fontSize: '14px', fontWeight: 700, marginBottom: subtitle ? '4px' : '0', color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}>
        {nodeLabel(data.raw).slice(0, 22)}
      </div>
      {subtitle && (
        <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.85)', textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}>
          {String(subtitle).slice(0, 26)}
        </div>
      )}
      {data.raw.status_detail && (
        <div style={{ fontSize: 10, fontWeight: 700, marginTop: 7, color: '#fff' }}>
          {String(data.raw.status_detail).slice(0, 34)}
        </div>
      )}
      {data.overlay?.labels?.length > 0 && (
        <div aria-label={`Causal overlay: ${data.overlay.labels.join(', ')}`} style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
          {data.overlay.labels.map(item => <span key={item} style={{ fontSize: 9, textTransform: 'uppercase' }}>{item}</span>)}
        </div>
      )}
      {hoverText && (
        <div className="topology-node-tooltip" role="tooltip">
          {hoverText.split('\n').map(line => (
            <div key={line}>{line}</div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: '#fff', width: 8, height: 8 }} />
    </div>
  );
};

const nodeTypes = {
  custom: CustomNode,
};

const getLayoutedElements = (nodes, edges, direction = 'TB') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));

  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, align: 'UL', marginx: 40, marginy: 40 });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: 220, height: 80 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    const newNode = { ...node };
    newNode.targetPosition = isHorizontal ? 'left' : 'top';
    newNode.sourcePosition = isHorizontal ? 'right' : 'bottom';
    newNode.position = {
      x: nodeWithPosition.x - 110,
      y: nodeWithPosition.y - 40,
    };
    return newNode;
  });

  return { nodes: newNodes, edges };
};

function Inspector({ node, degree }) {
  if (!node) {
    return <div style={{ color: 'var(--muted-2)', fontSize: 13 }}>Select a node to inspect its links and live metadata.</div>
  }

  const raw = node.data.raw;
  const preferred = [
    ['Type', nodeKind(raw)],
    ['Namespace', raw.namespace],
    ['Condition', raw.condition],
    ['Health reason', raw.health_reason?.replaceAll('_', ' ')],
    ['Status detail', raw.status_detail],
    ['Restarts', raw.restart_count],
    ['CPU %', raw.cpu_usage_pct ?? raw.cpu_pct],
    ['Memory %', raw.memory_usage_pct ?? raw.memory_pct],
    ['Service', raw.service_name],
    ['Host Node', raw.host_node],
    ['Operation', raw.operation],
    ['Duration', raw.duration_ms != null ? `${Number(raw.duration_ms).toFixed(1)} ms` : null],
    ['Status Code', raw.status_code],
  ].filter(([, value]) => value != null && value !== '')

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div style={{ fontSize: 16, fontWeight: 700 }}>{nodeLabel(raw)}</div>
        <Badge label={nodeKind(raw)} colour={nodeTone(raw)} />
      </div>
      {nodeSubtitle(raw) && <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 14 }}>{nodeSubtitle(raw)}</div>}
      <div style={{ color: 'var(--muted-2)', fontSize: 12, marginBottom: 14 }}>
        {degree.incoming} incoming · {degree.outgoing} outgoing connections
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 10 }}>
        {preferred.map(([label, value]) => (
          <div key={label}>
            <div style={{ color: 'var(--muted-2)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 2 }}>{label}</div>
            <div style={{ color: 'var(--text)', fontSize: 13, wordBreak: 'break-word' }}>{String(value)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TopologyGraph({ graph, focusId = null, height = 540, hypotheses = [], onNodeSelect = null }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState(focusId);
  const overlays = useMemo(() => overlayState(hypotheses), [hypotheses]);

  useEffect(() => {
    if (!graph) return;

    const initialNodes = (graph.nodes || []).map((node) => {
      const labels = [
        overlays?.roots.has(node.id) && 'root cause',
        overlays?.pathNodes.has(node.id) && 'causal path',
        overlays?.contradictions.has(node.id) && 'contradiction',
        overlays?.residuals.has(node.id) && 'unexplained residual',
      ].filter(Boolean)
      return {
      id: String(node.id),
      type: 'custom',
      data: {
        raw: node,
        overlay: overlays ? {
          root: overlays.roots.has(node.id),
          path: overlays.pathNodes.has(node.id),
          contradiction: overlays.contradictions.has(node.id),
          residual: overlays.residuals.has(node.id),
          labels,
        } : null,
      },
      position: { x: 0, y: 0 }
    }});

    const initialEdges = (graph.edges || []).map((edge, i) => {
      const path = overlays?.pathEdges.get(edge.id) || overlays?.pathEdges.get(`${edge.source}→${edge.target}`)
      const strength = Number(path?.evidence_strength)
      const stroke = path ? '#61dafb' : edgeStroke(edge)
      return {
      id: edge.id || `e-${edge.source}-${edge.target}-${i}`,
      source: edge.source,
      target: edge.target,
      label: edgeLabel(edge),
      animated: (edge.error_rate || 0) >= 0.05 || edge.edge_type === 'trace_call',
      data: { raw: edge, causalPath: path || null },
      style: {
        stroke,
        strokeWidth: path ? 2.5 + (Number.isFinite(strength) ? strength * 3 : 0) : 2,
      },
      labelStyle: { fill: 'var(--muted-2)', fontWeight: 600, fontSize: 11 },
      labelBgStyle: { fill: 'var(--bg-1)', fillOpacity: 0.8 },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: stroke,
      },
    }});

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      initialNodes,
      initialEdges,
      'TB'
    );

    setNodes(current => {
      const positions = new Map(current.map(node => [node.id, node.position]))
      return layoutedNodes.map(node => (
        positions.has(node.id) ? { ...node, position: positions.get(node.id) } : node
      ))
    });
    setEdges(layoutedEdges);
  }, [graph, overlays, setNodes, setEdges]);

  useEffect(() => {
    if (focusId) setSelectedId(focusId);
  }, [focusId]);

  const onNodeClick = useCallback((_, node) => {
    setSelectedId(node.id);
    if (typeof onNodeSelect === 'function') {
      onNodeSelect({ id: node.id, ...node.data })
    }
  }, [onNodeSelect]);

  const onPaneClick = useCallback(() => {
    setSelectedId(null);
  }, []);

  const selectedNode = nodes.find(node => node.id === selectedId) || null;
  const selectedDegree = {
    incoming: edges.filter(edge => edge.target === selectedId).length,
    outgoing: edges.filter(edge => edge.source === selectedId).length,
  };

  if (!graph) return null;

  if (nodes.length === 0) {
    return <Card><div style={{ color: 'var(--muted-2)', textAlign: 'center', padding: 36 }}>No graph data available for the current selection.</div></Card>
  }

  // Highlight logic: dim non-selected nodes/edges
  const displayNodes = nodes.map(n => ({
    ...n,
    selected: n.id === selectedId,
    style: { opacity: selectedId && n.id !== selectedId ? 0.4 : 1 }
  }));

  const displayEdges = edges.map(e => {
    const isConn = e.source === selectedId || e.target === selectedId;
    return {
      ...e,
      style: { ...e.style, opacity: selectedId && !isConn ? 0.3 : 1, strokeWidth: isConn ? 3 : 2 }
    };
  });

  return (
    <div className="topology-layout">
      <div className="topology-canvas" style={{ height }}>
        <ReactFlow
          nodes={displayNodes}
          edges={displayEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
          minZoom={0.2}
          maxZoom={2}
          attributionPosition="bottom-right"
        >
          <Background color="rgba(53, 82, 115, 0.4)" gap={20} size={1} />
          <Controls style={{ display: 'flex', flexDirection: 'column', gap: 5 }} />
          <Panel position="top-left" style={{ background: 'var(--bg-1)', padding: '6px 12px', borderRadius: 8, border: '1px solid var(--line)', color: 'var(--muted)', fontSize: 12 }}>
            {graph.title || 'Topology'}
            {graph.trace_id && <span style={{ color: 'var(--muted-2)' }}> · trace {String(graph.trace_id).slice(0, 16)}...</span>}
            {graph.report_id && <span style={{ color: 'var(--muted-2)' }}> · incident {String(graph.report_id).slice(0, 8)}</span>}
          </Panel>
          {graph.status_legend?.length > 0 && (
            <Panel position="top-right" style={{ background: 'var(--bg-1)', padding: 10, borderRadius: 8, border: '1px solid var(--line)', display: 'grid', gap: 6 }}>
              {graph.status_legend.map(item => (
                <div key={item.status} style={{ display: 'flex', alignItems: 'center', gap: 7, color: 'var(--muted)', fontSize: 10 }}>
                  <span aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, background: getToneStyles(nodeTone({ status: item.status }), false).background }} />
                  {item.label}
                </div>
              ))}
            </Panel>
          )}
        </ReactFlow>
      </div>

      <Card title="Inspector" style={{ marginBottom: 0 }}>
        <Inspector node={selectedNode} degree={selectedDegree} />
        <div style={{ marginTop: 18, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {['node', 'pod', 'service', 'span', 'infra'].map(kind => (
            <Badge key={kind} label={kind} colour={kind === 'pod' ? 'green' : kind === 'node' ? 'grey' : 'yellow'} />
          ))}
        </div>
        {overlays && (
          <aside aria-label="Topology overlay legend" style={{ marginTop: 18 }}>
            <strong>Causal overlay legend</strong>
            <ul>
              <li>Root cause — purple outline</li>
              <li>Causal path — cyan outline and connection</li>
              <li>Contradiction — dashed orange outline</li>
              <li>Unexplained residual — dotted yellow outline</li>
              <li>Connection width — evidence strength from persisted scoring basis</li>
            </ul>
          </aside>
        )}
      </Card>
      <details className="topology-text-summary" style={{ gridColumn: '1 / -1' }}>
        <summary>View topology as an accessible list</summary>
        <h3>Nodes</h3>
        <ul>
          {nodes.map(node => (
            <li key={node.id}>
              <strong>{nodeLabel(node.data.raw)}</strong>
              {' — '}{nodeKind(node.data.raw)}
              {nodeSubtitle(node.data.raw) ? ` — ${nodeSubtitle(node.data.raw)}` : ''}
              {node.data.overlay?.labels?.length ? ` — ${node.data.overlay.labels.join(', ')}` : ''}
            </li>
          ))}
        </ul>
        <h3>Connections</h3>
        <ul>
          {edges.map(edge => (
            <li key={edge.id}>
              {edge.source} to {edge.target}{edge.label ? ` — ${edge.label}` : ''}
              {edge.data?.causalPath ? ` — causal path, evidence strength ${Math.round(Number(edge.data.causalPath.evidence_strength || 0) * 100)}%` : ''}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
