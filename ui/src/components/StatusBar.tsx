import { useAppState } from '../hooks/useAppState';

export function StatusBar() {
  const { isConnected, dbType, graphData, lastQueryTime, schema } = useAppState();

  return (
    <footer className="flex items-center justify-between px-4 py-1.5 bg-[#0d0d16] border-t border-[#2a2a3a] text-[11px] text-gray-500">
      <div className="flex items-center gap-4">
        <span>{isConnected ? `Connected to ${dbType}` : 'Disconnected'}</span>
        {schema && (
          <span>
            Schema: {schema.nodeLabels.length} labels, {schema.edgeTypes.length} rel types
          </span>
        )}
      </div>
      <div className="flex items-center gap-4">
        {graphData && (
          <span>
            Showing {graphData.nodes.length} nodes, {graphData.edges.length} edges
          </span>
        )}
        {lastQueryTime !== null && <span>Query: {lastQueryTime}ms</span>}
      </div>
    </footer>
  );
}
