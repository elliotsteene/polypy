import { useOrderbookStream } from '../hooks/useOrderbookStream';
import { Metrics } from './Metrics';
import { PriceLevels } from './PriceLevels';
import type { Market } from '../types';

interface Props {
  assetId: string;
  market: Market;
}

export function OrderbookView({ assetId, market }: Props) {
  const { data, error, connected } = useOrderbookStream(assetId);

  const token = market.tokens.find(t => t.token_id === assetId);

  return (
    <div className="orderbook-view">
      <div className="header">
        <h2>{market.question}</h2>
        <span className="outcome">{token?.outcome}</span>
        <span className={`status ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? 'Live' : 'Disconnected'}
        </span>
      </div>

      {error && <div className="error">{error}</div>}

      {data && (
        <>
          <Metrics metrics={data.metrics} />
          <PriceLevels bids={data.bids} asks={data.asks} />
        </>
      )}
    </div>
  );
}
