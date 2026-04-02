import { useEffect, useCallback, useState } from 'react';
import { ZoomIn, ZoomOut, Maximize2, Focus, RotateCcw, Play, Pause } from 'lucide-react';
import { useSigma } from '../hooks/useSigma';
import { useAppState } from '../hooks/useAppState';
import { graphDataToGraphology } from '../lib/graph-adapter';

export function GraphCanvas() {
  const { graphData, setSelectedNode, selectedNode } = useAppState();
  const [hoveredNodeLabel, setHoveredNodeLabel] = useState<string | null>(null);
  const [graphStats, setGraphStats] = useState<{ nodes: number; edges: number } | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  const handleNodeClick = useCallback((nodeId: string) => {
    if (!graphData) return;
    const node = graphData.nodes.find(n => n.id === nodeId);
    if (node) setSelectedNode(node);
  }, [graphData, setSelectedNode]);

  const handleNodeHover = useCallback((nodeId: string | null) => {
    if (!nodeId || !graphData) {
      setHoveredNodeLabel(null);
      return;
    }
    const node = graphData.nodes.find(n => n.id === nodeId);
    if (node) {
      setHoveredNodeLabel(
        String(node.properties.name || node.properties.title || node.id)
      );
    }
  }, [graphData]);

  const handleStageClick = useCallback(() => {
    setSelectedNode(null);
  }, [setSelectedNode]);

  const {
    containerRef,
    setGraph: setSigmaGraph,
    zoomIn,
    zoomOut,
    resetZoom,
    focusNode,
    isLayoutRunning,
    startLayout,
    stopLayout,
    selectedNode: sigmaSelectedNode,
    setSelectedNode: setSigmaSelectedNode,
  } = useSigma({
    onNodeClick: handleNodeClick,
    onNodeHover: handleNodeHover,
    onStageClick: handleStageClick,
  });

  // When graphData changes, convert to graphology and render
  useEffect(() => {
    if (!graphData) {
      setGraphStats(null);
      setRenderError(null);
      return;
    }
    try {
      setRenderError(null);
      console.log('[GraphCanvas] graphData received:', graphData.nodes.length, 'nodes,', graphData.edges.length, 'edges');
      const sigmaGraph = graphDataToGraphology(graphData);
      console.log('[GraphCanvas] graphology graph created:', sigmaGraph.order, 'nodes,', sigmaGraph.size, 'edges');
      // Log container dimensions to catch zero-size issues
      const container = containerRef.current;
      if (container) {
        const rect = container.getBoundingClientRect();
        console.log('[GraphCanvas] container dimensions:', rect.width, 'x', rect.height);
        if (rect.width === 0 || rect.height === 0) {
          console.error('[GraphCanvas] Container has zero dimensions!');
          setRenderError('Container has zero dimensions');
        }
      }
      setGraphStats({ nodes: sigmaGraph.order, edges: sigmaGraph.size });
      setSigmaGraph(sigmaGraph);
    } catch (err) {
      console.error('[GraphCanvas] Failed to build graph:', err);
      setRenderError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, [graphData, setSigmaGraph, containerRef]);

  // Sync app selected node with sigma
  useEffect(() => {
    if (selectedNode) {
      setSigmaSelectedNode(selectedNode.id);
    } else {
      setSigmaSelectedNode(null);
    }
  }, [selectedNode, setSigmaSelectedNode]);

  const handleFocusSelected = useCallback(() => {
    if (selectedNode) focusNode(selectedNode.id);
  }, [selectedNode, focusNode]);

  const handleClearSelection = useCallback(() => {
    setSelectedNode(null);
    setSigmaSelectedNode(null);
    resetZoom();
  }, [setSelectedNode, setSigmaSelectedNode, resetZoom]);

  return (
    <div className="relative w-full h-full bg-[#0a0a12]">
      {/* Background gradient */}
      <div className="absolute inset-0 pointer-events-none">
        <div
          className="absolute inset-0"
          style={{
            background: `
              radial-gradient(circle at 50% 50%, rgba(99, 102, 241, 0.03) 0%, transparent 70%),
              linear-gradient(to bottom, #0a0a12, #0d0d18)
            `,
          }}
        />
      </div>

      {/* Sigma container */}
      <div
        ref={containerRef}
        className="w-full h-full cursor-grab active:cursor-grabbing"
      />

      {/* Empty state */}
      {!graphData && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center">
            <div className="text-gray-500 text-lg font-medium">No graph data</div>
            <div className="text-gray-600 text-sm mt-1">Run a Cypher query to visualize the graph</div>
          </div>
        </div>
      )}

      {/* Render error */}
      {renderError && (
        <div className="absolute top-4 left-4 px-3 py-2 bg-red-500/20 border border-red-500/40 rounded-lg z-20">
          <span className="text-xs text-red-400 font-mono">Render error: {renderError}</span>
        </div>
      )}

      {/* Graph stats badge */}
      {graphStats && !renderError && (
        <div className="absolute top-4 left-4 px-3 py-1.5 bg-[#12121c]/90 border border-[#2a2a3a] rounded-lg z-20 pointer-events-none">
          <span className="text-xs text-gray-400 font-mono">
            {graphStats.nodes.toLocaleString()} nodes &middot; {graphStats.edges.toLocaleString()} edges
          </span>
        </div>
      )}

      {/* Hovered node tooltip */}
      {hoveredNodeLabel && !sigmaSelectedNode && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 px-3 py-1.5 bg-[#12121c]/95 border border-[#2a2a3a] rounded-lg backdrop-blur-sm z-20 pointer-events-none">
          <span className="font-mono text-sm text-white">{hoveredNodeLabel}</span>
        </div>
      )}

      {/* Selection info bar */}
      {sigmaSelectedNode && selectedNode && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-2 bg-indigo-500/20 border border-indigo-500/30 rounded-xl backdrop-blur-sm z-20">
          <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse" />
          <span className="font-mono text-sm text-white">
            {String(selectedNode.properties.name || selectedNode.properties.title || selectedNode.id)}
          </span>
          <span className="text-xs text-gray-400">
            ({selectedNode.labels.join(', ')})
          </span>
          <button
            onClick={handleClearSelection}
            className="ml-2 px-2 py-0.5 text-xs text-gray-400 hover:text-white hover:bg-white/10 rounded transition-colors"
          >
            Clear
          </button>
        </div>
      )}

      {/* Graph Controls */}
      <div className="absolute bottom-4 right-4 flex flex-col gap-1 z-10">
        <button onClick={zoomIn} className="w-9 h-9 flex items-center justify-center bg-[#12121c] border border-[#2a2a3a] rounded-md text-gray-400 hover:bg-[#1a1a2a] hover:text-white transition-colors" title="Zoom In">
          <ZoomIn className="w-4 h-4" />
        </button>
        <button onClick={zoomOut} className="w-9 h-9 flex items-center justify-center bg-[#12121c] border border-[#2a2a3a] rounded-md text-gray-400 hover:bg-[#1a1a2a] hover:text-white transition-colors" title="Zoom Out">
          <ZoomOut className="w-4 h-4" />
        </button>
        <button onClick={resetZoom} className="w-9 h-9 flex items-center justify-center bg-[#12121c] border border-[#2a2a3a] rounded-md text-gray-400 hover:bg-[#1a1a2a] hover:text-white transition-colors" title="Fit to Screen">
          <Maximize2 className="w-4 h-4" />
        </button>

        <div className="h-px bg-[#2a2a3a] my-1" />

        {selectedNode && (
          <button onClick={handleFocusSelected} className="w-9 h-9 flex items-center justify-center bg-indigo-500/20 border border-indigo-500/30 rounded-md text-indigo-400 hover:bg-indigo-500/30 transition-colors" title="Focus Selected">
            <Focus className="w-4 h-4" />
          </button>
        )}

        {sigmaSelectedNode && (
          <button onClick={handleClearSelection} className="w-9 h-9 flex items-center justify-center bg-[#12121c] border border-[#2a2a3a] rounded-md text-gray-400 hover:bg-[#1a1a2a] hover:text-white transition-colors" title="Clear Selection">
            <RotateCcw className="w-4 h-4" />
          </button>
        )}

        <div className="h-px bg-[#2a2a3a] my-1" />

        <button
          onClick={isLayoutRunning ? stopLayout : startLayout}
          className={`w-9 h-9 flex items-center justify-center border rounded-md transition-all ${
            isLayoutRunning
              ? 'bg-indigo-500 border-indigo-500 text-white shadow-lg shadow-indigo-500/20 animate-pulse'
              : 'bg-[#12121c] border-[#2a2a3a] text-gray-400 hover:bg-[#1a1a2a] hover:text-white'
          }`}
          title={isLayoutRunning ? 'Stop Layout' : 'Run Layout'}
        >
          {isLayoutRunning ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
        </button>
      </div>

      {/* Layout indicator */}
      {isLayoutRunning && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-2 px-3 py-1.5 bg-emerald-500/20 border border-emerald-500/30 rounded-full backdrop-blur-sm z-10">
          <div className="w-2 h-2 bg-emerald-400 rounded-full animate-ping" />
          <span className="text-xs text-emerald-400 font-medium">Layout optimizing...</span>
        </div>
      )}
    </div>
  );
}
