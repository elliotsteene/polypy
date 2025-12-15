import { useEffect, useRef, useState } from 'react';
import type { OrderbookData, OrderbookMessage } from '../types';

export function useOrderbookStream(assetId: string | null) {
  const [data, setData] = useState<OrderbookData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.host}/ws/orderbook`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onmessage = (event) => {
      const msg: OrderbookMessage = JSON.parse(event.data);
      if (msg.type === 'orderbook' && msg.bids && msg.asks) {
        setData({
          asset_id: msg.asset_id!,
          bids: msg.bids,
          asks: msg.asks,
          metrics: msg.metrics || null,
          last_update_ts: msg.last_update_ts!,
        });
        setError(null);
      } else if (msg.type === 'error') {
        setError(msg.error || 'Unknown error');
      }
    };

    ws.onerror = () => setError('WebSocket error');
    ws.onclose = () => setConnected(false);

    return () => {
      ws.close();
    };
  }, []);

  // Subscribe to new asset when assetId changes
  useEffect(() => {
    if (connected && wsRef.current && assetId) {
      wsRef.current.send(JSON.stringify({ subscribe: assetId }));
    }
  }, [assetId, connected]);

  return { data, error, connected };
}
