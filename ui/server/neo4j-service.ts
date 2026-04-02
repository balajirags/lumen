import neo4j, { Driver, Session } from 'neo4j-driver';
import type { GraphData, GraphNode, GraphEdge, SchemaInfo, QueryResult } from './types.js';

let driver: Driver | null = null;
let currentDatabase: string = 'neo4j';

export async function connect(uri: string, username: string, password: string, database?: string): Promise<void> {
  if (driver) {
    await driver.close();
  }
  driver = neo4j.driver(uri, neo4j.auth.basic(username, password));
  currentDatabase = database || 'neo4j';
  // Verify connectivity
  await driver.verifyConnectivity();
}

export async function disconnect(): Promise<void> {
  if (driver) {
    await driver.close();
    driver = null;
  }
}

export function isConnected(): boolean {
  return driver !== null;
}

function getSession(): Session {
  if (!driver) throw new Error('Not connected to Neo4j');
  return driver.session({ database: currentDatabase });
}

export async function getSchema(): Promise<SchemaInfo> {
  const session = getSession();
  try {
    const labelsResult = await session.run('CALL db.labels()');
    const nodeLabels = labelsResult.records.map(r => r.get(0) as string);

    const typesResult = await session.run('CALL db.relationshipTypes()');
    const edgeTypes = typesResult.records.map(r => r.get(0) as string);

    const countResult = await session.run(
      'MATCH (n) RETURN count(n) as nodeCount'
    );
    const nodeCount = (countResult.records[0]?.get('nodeCount') as neo4j.Integer)?.toNumber() ?? 0;

    const edgeCountResult = await session.run(
      'MATCH ()-[r]->() RETURN count(r) as edgeCount'
    );
    const edgeCount = (edgeCountResult.records[0]?.get('edgeCount') as neo4j.Integer)?.toNumber() ?? 0;

    return { nodeLabels, edgeTypes, nodeCount, edgeCount };
  } finally {
    await session.close();
  }
}

function toPlainValue(val: unknown): unknown {
  if (val === null || val === undefined) return val;
  if (neo4j.isInt(val)) return (val as neo4j.Integer).toNumber();
  if (Array.isArray(val)) return val.map(toPlainValue);
  if (typeof val === 'object' && val !== null) {
    const obj: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(val)) {
      obj[k] = toPlainValue(v);
    }
    return obj;
  }
  return val;
}

export async function executeQuery(cypher: string, params?: Record<string, unknown>): Promise<QueryResult> {
  const session = getSession();
  const start = Date.now();
  try {
    const result = await session.run(cypher, params);
    const executionTime = Date.now() - start;

    const nodesMap = new Map<string, GraphNode>();
    const edgesMap = new Map<string, GraphEdge>();
    const rawRows: Record<string, unknown>[] = [];

    for (const record of result.records) {
      const row: Record<string, unknown> = {};

      for (const key of record.keys) {
        const val = record.get(key);
        row[key] = toPlainValue(val);

        // Extract nodes
        if (val && typeof val === 'object' && 'labels' in val && 'identity' in val && 'properties' in val) {
          const node = val as { identity: neo4j.Integer; labels: string[]; properties: Record<string, unknown> };
          const id = node.identity.toString();
          if (!nodesMap.has(id)) {
            nodesMap.set(id, {
              id,
              labels: node.labels,
              properties: toPlainValue(node.properties) as Record<string, unknown>,
            });
          }
        }

        // Extract relationships
        if (val && typeof val === 'object' && 'type' in val && 'start' in val && 'end' in val && 'identity' in val) {
          const rel = val as { identity: neo4j.Integer; type: string; start: neo4j.Integer; end: neo4j.Integer; properties: Record<string, unknown> };
          const id = rel.identity.toString();
          if (!edgesMap.has(id)) {
            edgesMap.set(id, {
              id,
              source: rel.start.toString(),
              target: rel.end.toString(),
              type: rel.type,
              properties: toPlainValue(rel.properties) as Record<string, unknown>,
            });
          }
        }

        // Extract paths
        if (val && typeof val === 'object' && 'segments' in val) {
          const path = val as { segments: Array<{ start: { identity: neo4j.Integer; labels: string[]; properties: Record<string, unknown> }; end: { identity: neo4j.Integer; labels: string[]; properties: Record<string, unknown> }; relationship: { identity: neo4j.Integer; type: string; start: neo4j.Integer; end: neo4j.Integer; properties: Record<string, unknown> } }> };
          for (const seg of path.segments) {
            const startId = seg.start.identity.toString();
            if (!nodesMap.has(startId)) {
              nodesMap.set(startId, {
                id: startId,
                labels: seg.start.labels,
                properties: toPlainValue(seg.start.properties) as Record<string, unknown>,
              });
            }
            const endId = seg.end.identity.toString();
            if (!nodesMap.has(endId)) {
              nodesMap.set(endId, {
                id: endId,
                labels: seg.end.labels,
                properties: toPlainValue(seg.end.properties) as Record<string, unknown>,
              });
            }
            const relId = seg.relationship.identity.toString();
            if (!edgesMap.has(relId)) {
              edgesMap.set(relId, {
                id: relId,
                source: seg.relationship.start.toString(),
                target: seg.relationship.end.toString(),
                type: seg.relationship.type,
                properties: toPlainValue(seg.relationship.properties) as Record<string, unknown>,
              });
            }
          }
        }
      }

      rawRows.push(row);
    }

    return {
      graph: {
        nodes: Array.from(nodesMap.values()),
        edges: Array.from(edgesMap.values()),
      },
      raw: rawRows,
      executionTime,
    };
  } finally {
    await session.close();
  }
}
