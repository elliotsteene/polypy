export interface Token {
  token_id: string;
  outcome: string;
}

export interface Market {
  condition_id: string;
  question: string;
  outcomes: string[];
  tokens: Token[];
  end_date_iso: string;
  end_timestamp: number;
}

export interface MarketsResponse {
  markets: Market[];
}

export interface OrderbookMetrics {
  spread: number | null;
  mid_price: number | null;
  imbalance: number;
}

export interface OrderbookData {
  asset_id: string;
  bids: [number, number][];  // [price, size]
  asks: [number, number][];
  metrics: OrderbookMetrics | null;
  last_update_ts: number;
}

export interface OrderbookMessage {
  type: 'orderbook' | 'error';
  asset_id?: string;
  bids?: [number, number][];
  asks?: [number, number][];
  metrics?: OrderbookMetrics;
  last_update_ts?: number;
  error?: string;
}
