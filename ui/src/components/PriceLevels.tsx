const PRICE_SCALE = 1000;
const SIZE_SCALE = 100;

interface Props {
  bids: [number, number][];
  asks: [number, number][];
}

export function PriceLevels({ bids, asks }: Props) {
  const formatPrice = (price: number) => (price / PRICE_SCALE).toFixed(3);
  const formatSize = (size: number) => (size / SIZE_SCALE).toFixed(2);

  // Find max size for bar width calculation
  const maxSize = Math.max(
    ...bids.map(([, s]) => s),
    ...asks.map(([, s]) => s),
    1
  );

  return (
    <div className="price-levels">
      <div className="asks">
        <h3>Asks</h3>
        {asks.slice().reverse().map(([price, size], i) => (
          <div key={i} className="level ask">
            <div
              className="bar"
              style={{ width: `${(size / maxSize) * 100}%` }}
            />
            <span className="price">{formatPrice(price)}</span>
            <span className="size">{formatSize(size)}</span>
          </div>
        ))}
      </div>
      <div className="bids">
        <h3>Bids</h3>
        {bids.map(([price, size], i) => (
          <div key={i} className="level bid">
            <div
              className="bar"
              style={{ width: `${(size / maxSize) * 100}%` }}
            />
            <span className="price">{formatPrice(price)}</span>
            <span className="size">{formatSize(size)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
