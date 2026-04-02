import type { ConnectionConfig, ApiResponse, QueryResult, SchemaInfo } from '../types';

const API_BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const json = await res.json() as ApiResponse<T>;
  if (!json.success) {
    throw new Error(json.error || 'Request failed');
  }
  return json.data as T;
}

export async function connect(config: ConnectionConfig): Promise<{ dbType: string }> {
  return request<{ dbType: string }>(`${API_BASE}/connect`, {
    method: 'POST',
    body: JSON.stringify(config),
  });
}

export async function disconnect(): Promise<void> {
  await request<void>(`${API_BASE}/disconnect`, { method: 'POST' });
}

export async function getSchema(): Promise<SchemaInfo> {
  return request<SchemaInfo>(`${API_BASE}/schema`);
}

export async function executeQuery(cypher: string): Promise<QueryResult> {
  return request<QueryResult>(`${API_BASE}/query`, {
    method: 'POST',
    body: JSON.stringify({ cypher }),
  });
}

export async function healthCheck(): Promise<{ ok: boolean; connected: string | null }> {
  const res = await fetch(`${API_BASE}/health`);
  return res.json();
}
