import { useState, type FormEvent } from 'react';
import { Database, Server, FolderOpen, Loader2, AlertCircle } from 'lucide-react';
import { useAppState } from '../hooks/useAppState';
import type { DbType, ConnectionConfig } from '../types';

export function ConnectionPanel() {
  const { connect, isConnecting, connectionError } = useAppState();
  const [dbType, setDbType] = useState<DbType>('neo4j');

  // Neo4j fields
  const [uri, setUri] = useState('bolt://localhost:7687');
  const [username, setUsername] = useState('neo4j');
  const [password, setPassword] = useState('');
  const [database, setDatabase] = useState('neo4j');

  // KuzuDB fields
  const [dbPath, setDbPath] = useState('');
  const [readOnly, setReadOnly] = useState(true);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const config: ConnectionConfig = { dbType };
    if (dbType === 'neo4j') {
      config.uri = uri;
      config.username = username;
      config.password = password;
      config.database = database;
    } else {
      config.dbPath = dbPath;
      config.readOnly = readOnly;
    }
    try {
      await connect(config);
    } catch {
      // Error is handled in state
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a12] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo / Title */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 mb-4">
            <Database className="w-8 h-8 text-indigo-400" />
          </div>
          <h1 className="text-2xl font-bold text-white">Code Graph UI</h1>
          <p className="text-sm text-gray-400 mt-1">Connect to Neo4j or KuzuDB to visualize your graph</p>
        </div>

        {/* Database Type Selector */}
        <div className="flex gap-2 mb-6">
          <button
            type="button"
            onClick={() => setDbType('neo4j')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border transition-all ${
              dbType === 'neo4j'
                ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-300'
                : 'bg-[#12121c] border-[#2a2a3a] text-gray-400 hover:border-[#3a3a4a] hover:text-gray-300'
            }`}
          >
            <Server className="w-4 h-4" />
            Neo4j
          </button>
          <button
            type="button"
            onClick={() => setDbType('kuzu')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg border transition-all ${
              dbType === 'kuzu'
                ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-300'
                : 'bg-[#12121c] border-[#2a2a3a] text-gray-400 hover:border-[#3a3a4a] hover:text-gray-300'
            }`}
          >
            <FolderOpen className="w-4 h-4" />
            KuzuDB
          </button>
        </div>

        {/* Connection Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="bg-[#12121c] border border-[#2a2a3a] rounded-xl p-5 space-y-4">
            {dbType === 'neo4j' ? (
              <>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">Connection URI</label>
                  <input
                    type="text"
                    value={uri}
                    onChange={e => setUri(e.target.value)}
                    placeholder="bolt://localhost:7687"
                    className="w-full px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
                    required
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">Username</label>
                    <input
                      type="text"
                      value={username}
                      onChange={e => setUsername(e.target.value)}
                      placeholder="neo4j"
                      className="w-full px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">Password</label>
                    <input
                      type="password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="w-full px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
                      required
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">Database</label>
                  <input
                    type="text"
                    value={database}
                    onChange={e => setDatabase(e.target.value)}
                    placeholder="neo4j"
                    className="w-full px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
                  />
                </div>
              </>
            ) : (
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">Database Path</label>
                <input
                  type="text"
                  value={dbPath}
                  onChange={e => setDbPath(e.target.value)}
                  placeholder="/path/to/kuzu/database"
                  className="w-full px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
                  required
                />
                <p className="text-xs text-gray-500 mt-1.5">Absolute path to the KuzuDB directory on the server</p>
                <div className="flex items-center gap-2 mt-3">
                  <input
                    type="checkbox"
                    id="readOnly"
                    checked={readOnly}
                    onChange={e => setReadOnly(e.target.checked)}
                    className="rounded border-[#2a2a3a] bg-[#0a0a12] text-indigo-500 focus:ring-indigo-500/20"
                  />
                  <label htmlFor="readOnly" className="text-xs text-gray-400">Open read-only (allows access when another process has the DB open)</label>
                </div>
              </div>
            )}
          </div>

          {connectionError && (
            <div className="flex items-center gap-2 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span className="text-sm text-red-300">{connectionError}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={isConnecting}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg transition-colors font-medium"
          >
            {isConnecting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Connecting...
              </>
            ) : (
              'Connect'
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
