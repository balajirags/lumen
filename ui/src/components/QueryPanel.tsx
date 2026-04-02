import { useState, type FormEvent, type KeyboardEvent } from 'react';
import { Play, Loader2, AlertCircle, Clock } from 'lucide-react';
import { useAppState } from '../hooks/useAppState';

const SAMPLE_QUERIES = [
  { label: 'All nodes (limit 100)', query: 'MATCH (n) RETURN n LIMIT 100' },
  { label: 'All with relationships', query: 'MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 200' },
  { label: 'Node labels', query: 'MATCH (n) RETURN DISTINCT labels(n) as labels, count(*) as count ORDER BY count DESC' },
  { label: 'Relationship types', query: 'MATCH ()-[r]->() RETURN DISTINCT type(r) as type, count(*) as count ORDER BY count DESC' },
];

export function QueryPanel() {
  const { executeQuery, isQuerying, queryError, lastQueryTime, schema } = useAppState();
  const [cypher, setCypher] = useState('MATCH (n)-[r]->(m) RETURN n, r, m');

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!cypher.trim()) return;
    try {
      await executeQuery(cypher);
    } catch {
      // Error handled in state
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Ctrl/Cmd + Enter to run query
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (!isQuerying && cypher.trim()) {
        executeQuery(cypher).catch(() => {});
      }
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Schema Info */}
      {schema && (
        <div className="px-4 py-3 border-b border-[#2a2a3a]">
          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>
              <span className="text-white font-medium">{schema.nodeCount.toLocaleString()}</span> nodes
            </span>
            <span>
              <span className="text-white font-medium">{schema.edgeCount.toLocaleString()}</span> edges
            </span>
            <span>
              <span className="text-white font-medium">{schema.nodeLabels.length}</span> labels
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {schema.nodeLabels.map(label => (
              <span
                key={label}
                className="px-2 py-0.5 bg-indigo-500/10 border border-indigo-500/20 rounded text-xs text-indigo-300"
              >
                {label}
              </span>
            ))}
            {schema.edgeTypes.map(type => (
              <span
                key={type}
                className="px-2 py-0.5 bg-purple-500/10 border border-purple-500/20 rounded text-xs text-purple-300"
              >
                {type}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Query Input */}
      <form onSubmit={handleSubmit} className="flex-1 flex flex-col px-4 py-3 gap-3">
        <div className="flex-1 relative">
          <textarea
            value={cypher}
            onChange={e => setCypher(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter Cypher query..."
            className="w-full h-full min-h-[100px] px-3 py-2 bg-[#0a0a12] border border-[#2a2a3a] rounded-lg text-sm text-white font-mono placeholder-gray-500 resize-none focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/20"
            spellCheck={false}
          />
          <div className="absolute bottom-2 right-2 text-[10px] text-gray-500">
            ⌘+Enter to run
          </div>
        </div>

        {/* Quick Queries */}
        <div className="flex flex-wrap gap-1.5">
          {SAMPLE_QUERIES.map(sq => (
            <button
              key={sq.label}
              type="button"
              onClick={() => setCypher(sq.query)}
              className="px-2 py-1 bg-[#12121c] border border-[#2a2a3a] rounded text-xs text-gray-400 hover:text-gray-200 hover:border-[#3a3a4a] transition-colors"
            >
              {sq.label}
            </button>
          ))}
        </div>

        {queryError && (
          <div className="flex items-start gap-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span className="text-xs text-red-300">{queryError}</span>
          </div>
        )}

        <div className="flex items-center justify-between">
          {lastQueryTime !== null && (
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <Clock className="w-3 h-3" />
              {lastQueryTime}ms
            </div>
          )}
          <button
            type="submit"
            disabled={isQuerying || !cypher.trim()}
            className="ml-auto flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg transition-colors text-sm font-medium"
          >
            {isQuerying ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Running...
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                Run Query
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
