import { useMarkets } from '../api/markets';
import type { Market, Token } from '../types';

interface Props {
  selectedAssetId: string | null;
  onSelectAsset: (assetId: string, market: Market) => void;
}

export function MarketList({ selectedAssetId, onSelectAsset }: Props) {
  const { data: markets, isLoading, error } = useMarkets();

  if (isLoading) return <div>Loading markets...</div>;
  if (error) return <div>Error loading markets</div>;

  return (
    <div className="market-list">
      <h2>Markets</h2>
      <select
        value={selectedAssetId || ''}
        onChange={(e) => {
          const assetId = e.target.value;
          const market = markets?.find(m =>
            m.tokens.some(t => t.token_id === assetId)
          );
          if (market) onSelectAsset(assetId, market);
        }}
      >
        <option value="">Select a market...</option>
        {markets?.map((market) => (
          <optgroup key={market.condition_id} label={market.question.slice(0, 50)}>
            {market.tokens.map((token: Token) => (
              <option key={token.token_id} value={token.token_id}>
                {token.outcome}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}
