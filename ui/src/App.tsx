import { AppStateProvider, useAppState } from './hooks/useAppState';
import { ConnectionPanel } from './components/ConnectionPanel';
import { Header } from './components/Header';
import { GraphCanvas } from './components/GraphCanvas';
import { QueryPanel } from './components/QueryPanel';
import { NodeDetailPanel } from './components/NodeDetailPanel';
import { StatusBar } from './components/StatusBar';

function AppContent() {
  const { isConnected, selectedNode } = useAppState();

  if (!isConnected) {
    return <ConnectionPanel />;
  }

  return (
    <div className="flex flex-col h-screen bg-[#0a0a12] overflow-hidden">
      <Header />
      <main className="flex-1 flex min-h-0">
        {/* Left Panel: Query */}
        <div className="w-96 bg-[#0d0d16] border-r border-[#2a2a3a] flex flex-col overflow-hidden">
          <div className="px-4 py-3 border-b border-[#2a2a3a]">
            <h2 className="text-sm font-semibold text-white">Query</h2>
          </div>
          <div className="flex-1 overflow-hidden">
            <QueryPanel />
          </div>
        </div>

        {/* Center: Graph */}
        <div className="flex-1 relative min-w-0">
          <GraphCanvas />
        </div>

        {/* Right Panel: Node Detail */}
        {selectedNode && <NodeDetailPanel />}
      </main>
      <StatusBar />
    </div>
  );
}

function App() {
  return (
    <AppStateProvider>
      <AppContent />
    </AppStateProvider>
  );
}

export default App;
