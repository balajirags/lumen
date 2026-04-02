// Shared types between server and client
export type DbType = 'neo4j' | 'kuzu';

export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface QueryResult {
  graph: GraphData;
  raw: Record<string, unknown>[];
  executionTime: number;
}

export interface SchemaInfo {
  nodeLabels: string[];
  edgeTypes: string[];
  nodeCount: number;
  edgeCount: number;
}
