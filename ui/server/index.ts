import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import * as neo4jService from './neo4j-service.js';
import * as kuzuService from './kuzu-service.js';
import type { DbType } from './types.js';

const app = express();
app.use(cors());
app.use(express.json());

let activeDb: DbType | null = null;

// Health check
app.get('/api/health', (_req, res) => {
  res.json({ ok: true, connected: activeDb, timestamp: new Date().toISOString() });
});

// Connect to a database
app.post('/api/connect', async (req, res) => {
  const { dbType, uri, username, password, database, dbPath, readOnly } = req.body as {
    dbType: DbType;
    uri?: string;
    username?: string;
    password?: string;
    database?: string;
    dbPath?: string;
    readOnly?: boolean;
  };

  try {
    // Disconnect existing
    if (activeDb === 'neo4j') await neo4jService.disconnect();
    if (activeDb === 'kuzu') await kuzuService.disconnect();

    if (dbType === 'neo4j') {
      if (!uri || !username || !password) {
        res.status(400).json({ success: false, error: 'Missing Neo4j connection parameters (uri, username, password)' });
        return;
      }
      await neo4jService.connect(uri, username, password, database);
      activeDb = 'neo4j';
    } else if (dbType === 'kuzu') {
      if (!dbPath) {
        res.status(400).json({ success: false, error: 'Missing KuzuDB path (dbPath)' });
        return;
      }
      await kuzuService.connect(dbPath, readOnly ?? false);
      activeDb = 'kuzu';
    } else {
      res.status(400).json({ success: false, error: `Unsupported dbType: ${String(dbType)}` });
      return;
    }

    res.json({ success: true, data: { dbType: activeDb } });
  } catch (err) {
    res.status(500).json({ success: false, error: err instanceof Error ? err.message : 'Connection failed' });
  }
});

// Disconnect
app.post('/api/disconnect', async (_req, res) => {
  try {
    if (activeDb === 'neo4j') await neo4jService.disconnect();
    if (activeDb === 'kuzu') await kuzuService.disconnect();
    activeDb = null;
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ success: false, error: err instanceof Error ? err.message : 'Disconnect failed' });
  }
});

// Get schema
app.get('/api/schema', async (_req, res) => {
  try {
    if (!activeDb) {
      res.status(400).json({ success: false, error: 'Not connected to any database' });
      return;
    }
    const schema = activeDb === 'neo4j'
      ? await neo4jService.getSchema()
      : await kuzuService.getSchema();
    res.json({ success: true, data: schema });
  } catch (err) {
    res.status(500).json({ success: false, error: err instanceof Error ? err.message : 'Failed to get schema' });
  }
});

// Execute query
app.post('/api/query', async (req, res) => {
  const { cypher } = req.body as { cypher: string };

  if (!cypher || typeof cypher !== 'string') {
    res.status(400).json({ success: false, error: 'Missing cypher query' });
    return;
  }

  try {
    if (!activeDb) {
      res.status(400).json({ success: false, error: 'Not connected to any database' });
      return;
    }

    // Inject safety LIMIT if query doesn't have one to prevent fetching entire DB
    const MAX_RESULTS = 5000;
    const hasLimit = /\bLIMIT\s+\d+/i.test(cypher);
    const safeCypher = hasLimit ? cypher : `${cypher.trimEnd()} LIMIT ${MAX_RESULTS}`;

    const result = activeDb === 'neo4j'
      ? await neo4jService.executeQuery(safeCypher)
      : await kuzuService.executeQuery(safeCypher);

    // Strip raw rows from response (unused by frontend) and add metadata
    const { raw, ...rest } = result;
    const response = {
      ...rest,
      totalNodes: result.graph.nodes.length,
      totalEdges: result.graph.edges.length,
      limitApplied: hasLimit ? undefined : MAX_RESULTS,
    };
    res.json({ success: true, data: response });
  } catch (err) {
    res.status(500).json({ success: false, error: err instanceof Error ? err.message : 'Query failed' });
  }
});

// In production (Docker), serve the built React app for all non-API routes
if (process.env.NODE_ENV === 'production') {
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  app.use(express.static(path.join(__dirname, '../dist')));
  app.get('*', (_req, res) => {
    res.sendFile(path.join(__dirname, '../dist/index.html'));
  });
}

const PORT = parseInt(process.env.PORT || '3001', 10);
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});

// Graceful shutdown: release database locks on exit
async function shutdown() {
  if (activeDb === 'neo4j') await neo4jService.disconnect();
  if (activeDb === 'kuzu') await kuzuService.disconnect();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
