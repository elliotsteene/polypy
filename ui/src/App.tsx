import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MarketList } from './components/MarketList';
import { OrderbookView } from './components/OrderbookView';
import type { Market } from './types';
import './App.css';

const queryClient = new QueryClient();

function AppContent() {
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [selectedMarket, setSelectedMarket] = useState<Market | null>(null);

  const handleSelectAsset = (assetId: string, market: Market) => {
    setSelectedAssetId(assetId);
    setSelectedMarket(market);
  };

  return (
    <div className="app">
      <h1>PolyPy Market Viewer</h1>
      <MarketList
        selectedAssetId={selectedAssetId}
        onSelectAsset={handleSelectAsset}
      />
      {selectedAssetId && selectedMarket && (
        <OrderbookView
          assetId={selectedAssetId}
          market={selectedMarket}
        />
      )}
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}
