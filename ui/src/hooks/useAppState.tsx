import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import type { ConnectionConfig, DbType, GraphData, SchemaInfo, GraphNode } from '../types';
import * as api from '../services/api';

interface AppState {
  // Connection
  isConnected: boolean;
  dbType: DbType | null;
  connectionConfig: ConnectionConfig | null;
  isConnecting: boolean;
  connectionError: string | null;
  connect: (config: ConnectionConfig) => Promise<void>;
  disconnectDb: () => Promise<void>;

  // Schema
  schema: SchemaInfo | null;
  loadSchema: () => Promise<void>;

  // Graph data
  graphData: GraphData | null;
  isQuerying: boolean;
  queryError: string | null;
  lastQueryTime: number | null;
  executeQuery: (cypher: string) => Promise<void>;

  // Selection
  selectedNode: GraphNode | null;
  setSelectedNode: (node: GraphNode | null) => void;

  // UI
  isSettingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
}

const AppStateContext = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [isConnected, setIsConnected] = useState(false);
  const [dbType, setDbType] = useState<DbType | null>(null);
  const [connectionConfig, setConnectionConfig] = useState<ConnectionConfig | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const [schema, setSchema] = useState<SchemaInfo | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [isQuerying, setIsQuerying] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [lastQueryTime, setLastQueryTime] = useState<number | null>(null);

  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [isSettingsOpen, setSettingsOpen] = useState(false);

  const connect = useCallback(async (config: ConnectionConfig) => {
    setIsConnecting(true);
    setConnectionError(null);
    try {
      await api.connect(config);
      setIsConnected(true);
      setDbType(config.dbType);
      setConnectionConfig(config);

      // Auto-load schema after connecting
      const schemaData = await api.getSchema();
      setSchema(schemaData);
    } catch (err) {
      setConnectionError(err instanceof Error ? err.message : 'Connection failed');
      throw err;
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const disconnectDb = useCallback(async () => {
    try {
      await api.disconnect();
    } catch {
      // ignore disconnect errors
    }
    setIsConnected(false);
    setDbType(null);
    setConnectionConfig(null);
    setSchema(null);
    setGraphData(null);
    setSelectedNode(null);
    setQueryError(null);
    setLastQueryTime(null);
  }, []);

  const loadSchema = useCallback(async () => {
    const schemaData = await api.getSchema();
    setSchema(schemaData);
  }, []);

  const executeQuery = useCallback(async (cypher: string) => {
    setIsQuerying(true);
    setQueryError(null);
    try {
      const result = await api.executeQuery(cypher);
      setGraphData(result.graph);
      setLastQueryTime(result.executionTime);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Query failed';
      // If server lost connection (e.g. after restart), reset to disconnected
      if (msg.includes('Not connected')) {
        setIsConnected(false);
        setDbType(null);
        setConnectionConfig(null);
        setSchema(null);
        setGraphData(null);
        setSelectedNode(null);
        setQueryError(null);
        setLastQueryTime(null);
        setConnectionError('Server connection lost. Please reconnect.');
        return;
      }
      setQueryError(msg);
      throw err;
    } finally {
      setIsQuerying(false);
    }
  }, []);

  return (
    <AppStateContext.Provider
      value={{
        isConnected,
        dbType,
        connectionConfig,
        isConnecting,
        connectionError,
        connect,
        disconnectDb,
        schema,
        loadSchema,
        graphData,
        isQuerying,
        queryError,
        lastQueryTime,
        executeQuery,
        selectedNode,
        setSelectedNode,
        isSettingsOpen,
        setSettingsOpen,
      }}
    >
      {children}
    </AppStateContext.Provider>
  );
}

export function useAppState(): AppState {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error('useAppState must be used within AppStateProvider');
  return ctx;
}
