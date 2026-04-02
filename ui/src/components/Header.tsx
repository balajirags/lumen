import { Database, LogOut, Settings } from 'lucide-react';
import { useAppState } from '../hooks/useAppState';

export function Header() {
  const { isConnected, dbType, disconnectDb, setSettingsOpen } = useAppState();

  return (
    <header className="flex items-center justify-between px-4 py-2 bg-[#0d0d16] border-b border-[#2a2a3a]">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Database className="w-5 h-5 text-indigo-400" />
          <span className="text-sm font-bold text-white">Code Graph UI</span>
        </div>
        {isConnected && dbType && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-emerald-500/10 border border-emerald-500/20 rounded-full">
            <div className="w-1.5 h-1.5 bg-emerald-400 rounded-full" />
            <span className="text-xs text-emerald-300 font-medium capitalize">{dbType}</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => setSettingsOpen(true)}
          className="p-2 text-gray-400 hover:text-white hover:bg-white/5 rounded-lg transition-colors"
          title="Settings"
        >
          <Settings className="w-4 h-4" />
        </button>
        {isConnected && (
          <button
            onClick={disconnectDb}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors"
            title="Disconnect"
          >
            <LogOut className="w-3.5 h-3.5" />
            Disconnect
          </button>
        )}
      </div>
    </header>
  );
}
