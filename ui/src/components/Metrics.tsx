import type { OrderbookMetrics } from '../types';

interface Props {
  metrics: OrderbookMetrics | null;
}

// Price scale factor (from protocol.py)
const PRICE_SCALE = 1000;

export function Metrics({ metrics }: Props) {
  if (!metrics) return null;

  const formatPrice = (price: number | null) =>
    price !== null ? (price / PRICE_SCALE).toFixed(3) : '-';

  const imbalancePercent = (metrics.imbalance * 100).toFixed(1);
  const imbalanceLabel = metrics.imbalance > 0.5 ? 'Bid heavy' : 'Ask heavy';

  return (
    <div className="metrics">
      <div className="metric">
        <span className="label">Spread</span>
        <span className="value">{formatPrice(metrics.spread)}</span>
      </div>
      <div className="metric">
        <span className="label">Mid Price</span>
        <span className="value">{formatPrice(metrics.mid_price)}</span>
      </div>
      <div className="metric">
        <span className="label">Imbalance</span>
        <span className="value">{imbalancePercent}% ({imbalanceLabel})</span>
      </div>
    </div>
  );
}
