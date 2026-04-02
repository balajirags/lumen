import { useRef, useEffect, useCallback, useState } from 'react';
import Sigma from 'sigma';
import Graph from 'graphology';
import FA2Layout from 'graphology-layout-forceatlas2/worker';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import noverlap from 'graphology-layout-noverlap';
import EdgeCurveProgram from '@sigma/edge-curve';
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from '../lib/graph-adapter';

// Thresholds for progressive degradation on large graphs
const LARGE_GRAPH_EDGES = 1000;
const LARGE_GRAPH_NODES = 800;

const hexToRgb = (hex: string) => {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? { r: parseInt(result[1], 16), g: parseInt(result[2], 16), b: parseInt(result[3], 16) }
    : { r: 100, g: 100, b: 100 };
};

const rgbToHex = (r: number, g: number, b: number) =>
  '#' + [r, g, b].map(x => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')).join('');

const dimColor = (hex: string, amount: number) => {
  const rgb = hexToRgb(hex);
  const bg = { r: 15, g: 15, b: 25 };
  return rgbToHex(bg.r + (rgb.r - bg.r) * amount, bg.g + (rgb.g - bg.g) * amount, bg.b + (rgb.b - bg.b) * amount);
};

const brightenColor = (hex: string, factor: number) => {
  const rgb = hexToRgb(hex);
  return rgbToHex(
    rgb.r + (255 - rgb.r) * (factor - 1) / factor,
    rgb.g + (255 - rgb.g) * (factor - 1) / factor,
    rgb.b + (255 - rgb.b) * (factor - 1) / factor
  );
};

interface UseSigmaOptions {
  onNodeClick?: (nodeId: string) => void;
  onNodeHover?: (nodeId: string | null) => void;
  onStageClick?: () => void;
}

export function useSigma(options: UseSigmaOptions = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null>(null);
  const layoutRef = useRef<FA2Layout | null>(null);
  const layoutTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectedNodeRef = useRef<string | null>(null);
  const [isLayoutRunning, setIsLayoutRunning] = useState(false);
  const [selectedNode, setSelectedNodeState] = useState<string | null>(null);

  const setSelectedNode = useCallback((nodeId: string | null) => {
    selectedNodeRef.current = nodeId;
    setSelectedNodeState(nodeId);
    sigmaRef.current?.refresh();
  }, []);

  // Initialize Sigma
  useEffect(() => {
    if (!containerRef.current) return;

    const graph = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>({ multi: true });
    graphRef.current = graph;

    const sigma = new Sigma(graph, containerRef.current, {
      renderLabels: true,
      labelFont: 'JetBrains Mono, ui-monospace, monospace',
      labelSize: 12,
      labelWeight: '500',
      labelColor: { color: '#e4e4ed' },
      labelRenderedSizeThreshold: 5,
      labelDensity: 0.25,
      labelGridCellSize: 120,
      defaultNodeColor: '#6b7280',
      defaultEdgeColor: '#3a3a5a',
      defaultEdgeType: 'curved',
      edgeProgramClasses: { curved: EdgeCurveProgram },
      minCameraRatio: 0.002,
      maxCameraRatio: 50,
      hideEdgesOnMove: true,
      zIndex: true,

      defaultDrawNodeHover: (context, data, settings) => {
        const label = data.label;
        if (!label) return;
        const size = settings.labelSize || 11;
        const font = settings.labelFont || 'monospace';
        const weight = settings.labelWeight || '500';
        context.font = `${weight} ${size}px ${font}`;
        const textWidth = context.measureText(label).width;
        const nodeSize = data.size || 8;
        const x = data.x;
        const y = data.y - nodeSize - 10;
        const paddingX = 8;
        const paddingY = 5;
        const height = size + paddingY * 2;
        const width = textWidth + paddingX * 2;
        const radius = 4;

        context.fillStyle = '#0f0f19';
        context.beginPath();
        context.roundRect(x - width / 2, y - height / 2, width, height, radius);
        context.fill();
        context.strokeStyle = data.color || '#6366f1';
        context.lineWidth = 2;
        context.stroke();
        context.fillStyle = '#f5f5f7';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(label, x, y);

        context.beginPath();
        context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
        context.strokeStyle = data.color || '#6366f1';
        context.lineWidth = 2;
        context.globalAlpha = 0.5;
        context.stroke();
        context.globalAlpha = 1;
      },

      nodeReducer: (node, data) => {
        const res = { ...data };
        if (data.hidden) { res.hidden = true; return res; }
        const currentSelected = selectedNodeRef.current;

        if (currentSelected) {
          const g = graphRef.current;
          if (g) {
            const isSelected = node === currentSelected;
            const isNeighbor = g.hasEdge(node, currentSelected) || g.hasEdge(currentSelected, node);
            if (isSelected) {
              res.color = data.color;
              res.size = (data.size || 8) * 1.8;
              res.zIndex = 2;
              res.highlighted = true;
            } else if (isNeighbor) {
              res.color = data.color;
              res.size = (data.size || 8) * 1.3;
              res.zIndex = 1;
            } else {
              res.color = dimColor(data.color, 0.25);
              res.size = (data.size || 8) * 0.6;
              res.zIndex = 0;
            }
          }
        }
        return res;
      },

      edgeReducer: (edge, data) => {
        const res = { ...data };
        const currentSelected = selectedNodeRef.current;
        if (currentSelected) {
          const g = graphRef.current;
          if (g) {
            const [source, target] = g.extremities(edge);
            const isConnected = source === currentSelected || target === currentSelected;
            if (isConnected) {
              res.color = brightenColor(data.color, 1.5);
              res.size = Math.max(3, (data.size || 1) * 4);
              res.zIndex = 2;
            } else {
              res.color = dimColor(data.color, 0.1);
              res.size = 0.3;
              res.zIndex = 0;
            }
          }
        }
        return res;
      },
    });

    sigmaRef.current = sigma;

    // Detect WebGL context loss
    const canvas = containerRef.current.querySelector('canvas');
    if (canvas) {
      canvas.addEventListener('webglcontextlost', (e) => {
        console.error('[Sigma] WebGL context lost!', e);
      });
      canvas.addEventListener('webglcontextrestored', () => {
        console.log('[Sigma] WebGL context restored');
      });
    }

    sigma.on('clickNode', ({ node }) => {
      setSelectedNode(node);
      options.onNodeClick?.(node);
    });

    sigma.on('clickStage', () => {
      setSelectedNode(null);
      options.onStageClick?.();
    });

    sigma.on('enterNode', ({ node }) => {
      options.onNodeHover?.(node);
      if (containerRef.current) containerRef.current.style.cursor = 'pointer';
    });

    sigma.on('leaveNode', () => {
      options.onNodeHover?.(null);
      if (containerRef.current) containerRef.current.style.cursor = 'grab';
    });

    return () => {
      if (layoutTimeoutRef.current) clearTimeout(layoutTimeoutRef.current);
      layoutRef.current?.kill();
      sigma.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runLayout = useCallback((graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>) => {
    const nodeCount = graph.order;
    if (nodeCount === 0) return;

    if (layoutRef.current) { layoutRef.current.kill(); layoutRef.current = null; }
    if (layoutTimeoutRef.current) { clearTimeout(layoutTimeoutRef.current); layoutTimeoutRef.current = null; }

    const inferredSettings = forceAtlas2.inferSettings(graph);
    const isSmall = nodeCount < 500;
    const isMedium = nodeCount >= 500 && nodeCount < 2000;
    const isLarge = nodeCount >= 2000;

    const settings = {
      ...inferredSettings,
      gravity: isSmall ? 0.5 : isMedium ? 0.3 : isLarge ? 0.15 : 0.1,
      scalingRatio: isSmall ? 20 : isMedium ? 40 : isLarge ? 80 : 120,
      slowDown: isSmall ? 2 : isMedium ? 5 : isLarge ? 8 : 12,
      barnesHutOptimize: nodeCount > 100,
      barnesHutTheta: isLarge ? 0.8 : 0.5,
      strongGravityMode: false,
      outboundAttractionDistribution: true,
      linLogMode: false,
      adjustSizes: true,
      edgeWeightInfluence: 1,
    };

    try {
      const layout = new FA2Layout(graph, { settings });
      layoutRef.current = layout;
      layout.start();
      setIsLayoutRunning(true);
      console.log('[Sigma] FA2 layout started for', nodeCount, 'nodes');
    } catch (err) {
      console.error('[Sigma] FA2 layout failed to start:', err);
      // Graph will render with initial spiral positions — still usable
      sigmaRef.current?.refresh();
      return;
    }

    const duration = nodeCount > 5000 ? 20000 : nodeCount > 1000 ? 8000 : 5000;
    layoutTimeoutRef.current = setTimeout(() => {
      if (layoutRef.current) {
        layoutRef.current.stop();
        layoutRef.current = null;
        // Skip noverlap for large graphs — it's synchronous and blocks the main thread
        if (nodeCount <= LARGE_GRAPH_NODES) {
          noverlap.assign(graph, { maxIterations: 20, settings: { ratio: 1.1, margin: 10, expansion: 1.05 } });
        }
        sigmaRef.current?.refresh();
        // Re-fit camera after layout settles
        sigmaRef.current?.getCamera().animatedReset({ duration: 300 });
        setIsLayoutRunning(false);
        console.log('[Sigma] layout finished for', nodeCount, 'nodes');
      }
    }, duration);
  }, []);

  const setGraph = useCallback((newGraph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>) => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    if (layoutRef.current) { layoutRef.current.kill(); layoutRef.current = null; }
    if (layoutTimeoutRef.current) { clearTimeout(layoutTimeoutRef.current); layoutTimeoutRef.current = null; }

    const nodeCount = newGraph.order;
    const edgeCount = newGraph.size;

    // Adapt render settings for large graphs
    sigma.setSetting('hideEdgesOnMove', edgeCount > LARGE_GRAPH_EDGES);
    sigma.setSetting('labelRenderedSizeThreshold', nodeCount > LARGE_GRAPH_NODES ? 10 : 5);
    sigma.setSetting('labelDensity', nodeCount > LARGE_GRAPH_NODES ? 0.1 : 0.25);
    // Switch to built-in line program for large graphs (EdgeCurveProgram is too heavy)
    sigma.setSetting('defaultEdgeType', edgeCount > LARGE_GRAPH_EDGES ? 'line' : 'curved');

    try {
      graphRef.current = newGraph;
      console.log('[Sigma] setGraph called with', newGraph.order, 'nodes,', newGraph.size, 'edges');
      sigma.setGraph(newGraph);
      console.log('[Sigma] setGraph succeeded');
      setSelectedNode(null);
      runLayout(newGraph);
      console.log('[Sigma] layout started');
      sigma.getCamera().animatedReset({ duration: 500 });
    } catch (err) {
      console.error('[Sigma] Failed to render graph:', err);
    }
  }, [runLayout, setSelectedNode]);

  const focusNode = useCallback((nodeId: string) => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !graph.hasNode(nodeId)) return;
    selectedNodeRef.current = nodeId;
    setSelectedNodeState(nodeId);
    const nodeDisplayData = sigma.getNodeDisplayData(nodeId);
    if (nodeDisplayData) {
      sigma.getCamera().animate({ x: nodeDisplayData.x, y: nodeDisplayData.y, ratio: 0.15 }, { duration: 400 });
    }
    sigma.refresh();
  }, []);

  const zoomIn = useCallback(() => { sigmaRef.current?.getCamera().animatedZoom({ duration: 200 }); }, []);
  const zoomOut = useCallback(() => { sigmaRef.current?.getCamera().animatedUnzoom({ duration: 200 }); }, []);
  const resetZoom = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: 300 });
    setSelectedNode(null);
  }, [setSelectedNode]);

  const startLayout = useCallback(() => {
    const graph = graphRef.current;
    if (!graph || graph.order === 0) return;
    runLayout(graph);
  }, [runLayout]);

  const stopLayout = useCallback(() => {
    if (layoutTimeoutRef.current) { clearTimeout(layoutTimeoutRef.current); layoutTimeoutRef.current = null; }
    if (layoutRef.current) {
      layoutRef.current.stop();
      layoutRef.current = null;
      const graph = graphRef.current;
      if (graph && graph.order <= LARGE_GRAPH_NODES) {
        noverlap.assign(graph, { maxIterations: 20, settings: { ratio: 1.1, margin: 10, expansion: 1.05 } });
        sigmaRef.current?.refresh();
      }
      setIsLayoutRunning(false);
    }
  }, []);

  return {
    containerRef,
    sigmaRef,
    graphRef,
    setGraph,
    zoomIn,
    zoomOut,
    resetZoom,
    focusNode,
    isLayoutRunning,
    startLayout,
    stopLayout,
    selectedNode,
    setSelectedNode,
  };
}
