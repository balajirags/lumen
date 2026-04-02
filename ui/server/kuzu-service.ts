import type { GraphData, GraphNode, GraphEdge, SchemaInfo, QueryResult } from './types.js';

// Dynamic import for kuzu (CommonJS module)
let kuzuMod: typeof import('kuzu') | null = null;
let db: InstanceType<typeof import('kuzu').Database> | null = null;
let conn: InstanceType<typeof import('kuzu').Connection> | null = null;

async function loadKuzu() {
  if (!kuzuMod) {
    kuzuMod = await import('kuzu');
  }
  return kuzuMod;
}

export async function connect(dbPath: string, readOnly = false): Promise<void> {
  await disconnect();
  const mod = await loadKuzu();
  const newDb = new mod.Database(dbPath, 0, true, readOnly);
  const newConn = new mod.Connection(newDb);
  // Eagerly initialize to surface errors (e.g. lock, missing file) immediately
  try {
    await newDb.init();
  } catch (err) {
    try { await newDb.close(); } catch { /* ignore cleanup error */ }
    throw err;
  }
  db = newDb;
  conn = newConn;
}

export async function disconnect(): Promise<void> {
  const c = conn;
  const d = db;
  conn = null;
  db = null;
  if (c) {
    try { await c.close(); } catch { /* ignore */ }
  }
  if (d) {
    try { await d.close(); } catch { /* ignore */ }
  }
}

export function isConnected(): boolean {
  return conn !== null;
}

async function query(cypher: string): Promise<Record<string, unknown>[]> {
  if (!conn) throw new Error('Not connected to KuzuDB');
  const result = await conn.query(cypher);
  const rows = await result.getAll();
  if (Array.isArray(rows)) {
    return rows as Record<string, unknown>[];
  }
  return [];
}

export async function getSchema(): Promise<SchemaInfo> {
  if (!conn) throw new Error('Not connected to KuzuDB');

  let nodeLabels: string[] = [];
  let edgeTypes: string[] = [];
  let nodeCount = 0;
  let edgeCount = 0;

  try {
    const nodeTableRows = await query("CALL show_tables() RETURN *");
    for (const row of nodeTableRows) {
      const tableType = row.type as string;
      const tableName = row.name as string;
      if (tableType === 'NODE') {
        nodeLabels.push(tableName);
      } else if (tableType === 'REL') {
        edgeTypes.push(tableName);
      }
    }
  } catch {
    // Fallback: try older KuzuDB API
    try {
      const ntRows = await query("CALL show_tables() RETURN *");
      nodeLabels = ntRows.map(r => String(r.name));
    } catch {
      // ignore
    }
  }

  // Count nodes
  for (const label of nodeLabels) {
    try {
      const countRows = await query(`MATCH (n:${label}) RETURN count(n) as cnt`);
      if (countRows.length > 0) {
        nodeCount += Number(countRows[0].cnt) || 0;
      }
    } catch {
      // skip
    }
  }

  // Count edges
  for (const edgeType of edgeTypes) {
    try {
      const countRows = await query(`MATCH ()-[r:${edgeType}]->() RETURN count(r) as cnt`);
      if (countRows.length > 0) {
        edgeCount += Number(countRows[0].cnt) || 0;
      }
    } catch {
      // skip
    }
  }

  return { nodeLabels, edgeTypes, nodeCount, edgeCount };
}

export async function executeQuery(cypher: string): Promise<QueryResult> {
  if (!conn) throw new Error('Not connected to KuzuDB');

  const start = Date.now();
  const result = await conn.query(cypher);
  const executionTime = Date.now() - start;

  const nodesMap = new Map<string, GraphNode>();
  const edgesMap = new Map<string, GraphEdge>();
  const rawRows: Record<string, unknown>[] = [];

  const table = await result.getAll();
  if (Array.isArray(table)) {
    for (const row of table) {
      for (const [, val] of Object.entries(row as Record<string, unknown>)) {
        extractGraphElements(val, nodesMap, edgesMap);
      }
    }
  }

  return {
    graph: {
      nodes: Array.from(nodesMap.values()),
      edges: Array.from(edgesMap.values()),
    },
    raw: rawRows,
    executionTime,
  };
}

function makeNodeId(idObj: unknown): string {
  if (idObj && typeof idObj === 'object' && 'table' in (idObj as Record<string, unknown>) && 'offset' in (idObj as Record<string, unknown>)) {
    const { table, offset } = idObj as Record<string, unknown>;
    return `${table}:${offset}`;
  }
  return String(idObj);
}

function extractGraphElements(
  val: unknown,
  nodesMap: Map<string, GraphNode>,
  edgesMap: Map<string, GraphEdge>,
): void {
  if (!val || typeof val !== 'object') return;
  const obj = val as Record<string, unknown>;

  // KuzuDB node: has _id (with table/offset) and _label
  if ('_id' in obj && '_label' in obj && !('_src' in obj) && !('_dst' in obj)) {
    const nodeId = makeNodeId(obj._id);
    if (!nodesMap.has(nodeId)) {
      const properties: Record<string, unknown> = {};
      for (const [pk, pv] of Object.entries(obj)) {
        if (!pk.startsWith('_')) {
          // Truncate large string properties to keep payloads manageable
          if (typeof pv === 'string' && pv.length > 500) {
            properties[pk] = pv.slice(0, 500) + '…';
          } else {
            properties[pk] = pv;
          }
        }
      }
      nodesMap.set(nodeId, {
        id: nodeId,
        labels: [String(obj._label)],
        properties,
      });
    }
    return;
  }

  // KuzuDB relationship: has _src, _dst, _label
  if ('_src' in obj && '_dst' in obj && '_label' in obj) {
    const srcId = makeNodeId(obj._src);
    const dstId = makeNodeId(obj._dst);
    const edgeId = obj._id ? makeNodeId(obj._id) : `${srcId}-${obj._label}-${dstId}`;
    if (!edgesMap.has(edgeId)) {
      const properties: Record<string, unknown> = {};
      for (const [pk, pv] of Object.entries(obj)) {
        if (!pk.startsWith('_')) {
          properties[pk] = pv;
        }
      }
      edgesMap.set(edgeId, {
        id: edgeId,
        source: srcId,
        target: dstId,
        type: String(obj._label),
        properties,
      });
    }
    return;
  }

  // Recurse into nested structures (e.g. path results with _nodes and _rels)
  if ('_nodes' in obj && '_rels' in obj) {
    const nodes = obj._nodes;
    const rels = obj._rels;
    if (Array.isArray(nodes)) {
      for (const n of nodes) extractGraphElements(n, nodesMap, edgesMap);
    }
    if (Array.isArray(rels)) {
      for (const r of rels) extractGraphElements(r, nodesMap, edgesMap);
    }
  }
}
