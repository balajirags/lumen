import { X } from 'lucide-react';
import { useAppState } from '../hooks/useAppState';

export function NodeDetailPanel() {
  const { selectedNode, setSelectedNode } = useAppState();

  if (!selectedNode) return null;

  const displayName = String(
    selectedNode.properties.name || selectedNode.properties.title || selectedNode.id
  );

  return (
    <div className="w-80 bg-[#12121c] border-l border-[#2a2a3a] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a2a3a]">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-white truncate">{displayName}</h3>
          <div className="flex gap-1.5 mt-1">
            {selectedNode.labels.map(label => (
              <span
                key={label}
                className="px-2 py-0.5 bg-indigo-500/10 border border-indigo-500/20 rounded text-[10px] text-indigo-300"
              >
                {label}
              </span>
            ))}
          </div>
        </div>
        <button
          onClick={() => setSelectedNode(null)}
          className="p-1 text-gray-400 hover:text-white hover:bg-white/10 rounded transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Properties */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <h4 className="text-xs text-gray-400 uppercase tracking-wider mb-2">Properties</h4>
        <div className="space-y-2">
          <div className="flex justify-between items-start">
            <span className="text-xs text-gray-500 font-mono">id</span>
            <span className="text-xs text-gray-300 font-mono text-right max-w-[60%] break-all">{selectedNode.id}</span>
          </div>
          {Object.entries(selectedNode.properties).map(([key, value]) => (
            <div key={key} className="flex justify-between items-start">
              <span className="text-xs text-gray-500 font-mono">{key}</span>
              <span className="text-xs text-gray-300 font-mono text-right max-w-[60%] break-all">
                {typeof value === 'object' ? JSON.stringify(value) : String(value)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
