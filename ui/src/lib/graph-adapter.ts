import Graph from 'graphology';
import type { GraphData } from '../types';
import { getLabelColor, getEdgeColor } from './constants';

export interface SigmaNodeAttributes {
  x: number;
  y: number;
  size: number;
  color: string;
  label: string;
  nodeType: string;
  hidden?: boolean;
  zIndex?: number;
  highlighted?: boolean;
  mass?: number;
}

export interface SigmaEdgeAttributes {
  size: number;
  color: string;
  relationType: string;
  type?: string;
  curvature?: number;
  zIndex?: number;
  hidden?: boolean;
}

/**
 * Convert API GraphData to a Graphology graph for Sigma.js rendering.
 * Inspired by GitNexus's graph-adapter with radial/hierarchy positioning.
 */
export function graphDataToGraphology(data: GraphData): Graph<SigmaNodeAttributes, SigmaEdgeAttributes> {
  const graph = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>({ multi: true });
  const nodeCount = data.nodes.length;
  if (nodeCount === 0) return graph;

  // Build adjacency info for degree-aware sizing
  const degreeMap = new Map<string, number>();
  for (const edge of data.edges) {
    degreeMap.set(edge.source, (degreeMap.get(edge.source) || 0) + 1);
    degreeMap.set(edge.target, (degreeMap.get(edge.target) || 0) + 1);
  }

  // Spread factor based on node count
  const spread = Math.max(300, Math.sqrt(nodeCount) * 60);

  // Position nodes using golden angle spiral for even distribution
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));

  data.nodes.forEach((node, index) => {
    const angle = index * goldenAngle;
    const radius = spread * Math.sqrt((index + 1) / Math.max(nodeCount, 1));
    const jitter = spread * 0.08;
    const x = radius * Math.cos(angle) + (Math.random() - 0.5) * jitter;
    const y = radius * Math.sin(angle) + (Math.random() - 0.5) * jitter;

    const primaryLabel = node.labels[0] || 'Unknown';
    const displayName = String(
      node.properties.name || node.properties.title || node.properties.id || node.id
    );

    // Degree-aware sizing: larger nodes for more connections
    const degree = degreeMap.get(node.id) || 0;
    const baseSize = nodeCount > 2000 ? 4 : nodeCount > 500 ? 6 : 8;
    const sizeScale = 1 + Math.log2(degree + 1) * 0.6;
    const size = baseSize * sizeScale;

    // Mass for ForceAtlas2 — high-degree nodes resist movement
    const mass = 1 + degree * 0.5;

    graph.addNode(node.id, {
      x,
      y,
      size,
      color: getLabelColor(primaryLabel),
      label: displayName,
      nodeType: primaryLabel,
      hidden: false,
      mass,
    });
  });

  // Add edges with adaptive sizing
  // Use straight lines for large graphs to reduce GPU memory pressure
  const edgeCount = data.edges.length;
  const edgeBaseSize = nodeCount > 5000 ? 0.5 : nodeCount > 1000 ? 1 : 1.5;
  const useCurved = edgeCount <= 1000;

  data.edges.forEach((edge) => {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      const edgeKey = `${edge.source}-${edge.type}-${edge.target}`;
      if (!graph.hasEdge(edgeKey)) {
        graph.addEdgeWithKey(edgeKey, edge.source, edge.target, {
          size: edgeBaseSize,
          color: getEdgeColor(edge.type),
          relationType: edge.type,
          ...(useCurved
            ? { type: 'curved', curvature: 0.15 + Math.random() * 0.1 }
            : { type: 'line' }),
        });
      }
    }
  });

  return graph;
}
