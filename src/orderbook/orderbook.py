import time
from dataclasses import dataclass, field

from sortedcontainers import SortedDict

from src.messages.protocol import BookSnapshot, PriceChange, PriceLevel, Side


@dataclass(slots=True)
class OrderbookState:
    """
    Single orderbook state container
    """

    asset_id: str
    market: str

    # Bids: highest price first (negative keys for reverse sort)
    # Key: -price (negated for descending order)
    # Value: size at that price level
    _bids: SortedDict = field(default_factory=SortedDict)

    # Asks: lowest price first (natural ascending order)
    # Key: price
    # Value: size at that price level
    _asks: SortedDict = field(default_factory=SortedDict)

    last_hash: str = ""
    last_update_ts: int = 0
    local_update_ts: float = 0.0

    # Cached computations (invalidated on update)
    _cached_best_bid: int | None = None
    _cached_best_ask: int | None = None
    _cache_valid: bool = False

    def apply_snapshot(self, snapshot: BookSnapshot, timestamp: int) -> None:
        """
        Apply full book snapshot, replacing existing state
        """
        self._bids.clear()
        self._asks.clear()

        for level in snapshot.bids:
            if level.size > 0:
                self._bids[-level.price] = level.size  # Negate for desc sort

        for level in snapshot.asks:
            if level.size > 0:
                self._asks[level.price] = level.size

        self.last_hash = snapshot.hash
        self.last_update_ts = timestamp
        self.local_update_ts = time.monotonic()
        self._invalidate_cache()

    def apply_price_change(self, price_change: PriceChange, timestamp: int) -> None:
        """
        Apply single price level change

        Size of 0 means remove the level
        """
        if price_change.side == Side.BUY:
            key = -price_change.price  # Negated for bid ordering
            self._update_price_level(
                key=key,
                size=price_change.size,
                price_map=self._bids,
            )
        else:
            key = price_change.price
            self._update_price_level(
                key=key,
                size=price_change.size,
                price_map=self._asks,
            )

        self._set_best_bid(price_change.best_bid)
        self._set_best_ask(price_change.best_ask)
        self._cache_valid = True

        self.last_hash = price_change.hash
        self.last_update_ts = timestamp
        self.local_update_ts = time.monotonic()

    @staticmethod
    def _update_price_level(key: int, size: int, price_map: SortedDict) -> None:
        if size == 0:
            price_map.pop(key, None)
        else:
            price_map[key] = size

    def _set_best_bid(self, best_bid: int | None) -> None:
        self._cached_best_bid = best_bid

    def _set_best_ask(self, best_ask: int | None) -> None:
        self._cached_best_ask = best_ask

    def _invalidate_cache(self) -> None:
        self._cache_valid = False
        self._cached_best_bid = None
        self._cached_best_ask = None

    @property
    def best_bid(self) -> int | None:
        if not self._cache_valid:
            self._recompute_cache
        return self._cached_best_bid

    @property
    def best_ask(self) -> int | None:
        if not self._cache_valid:
            self._recompute_cache
        return self._cached_best_ask

    @property
    def spread(self) -> int | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return ask - bid

        return None

    @property
    def mid_price(self) -> int | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return (bid + ask) // 2

        return None

    def get_bids(self, depth: int = 10) -> list[PriceLevel]:
        if depth == 0:
            return []

        result = []
        for neg_price, size in self._bids.items()[:depth]:
            result.append(PriceLevel(price=-neg_price, size=size))

        return result

    def get_asks(self, depth: int = 10) -> list[PriceLevel]:
        if depth == 0:
            return []

        result = []
        for price, size in self._asks.items()[:depth]:
            result.append(PriceLevel(price=price, size=size))

        return result

    def _recompute_cache(self) -> None:
        # First key is most negative = highest price
        best_bid: int | None = -self._bids.keys()[0] if self._bids else None
        self._set_best_bid(best_bid)

        best_ask: int | None = self._asks.keys()[0] if self._asks else None
        self._set_best_ask(best_ask)

        self._cache_valid = True

    def __sizeof__(self) -> int:
        """Approx memory usage"""
        base = object.__sizeof__(self) + 8 * 10

        bids_size = 64 + len(self._bids) * 16
        ask_size = 64 + len(self._asks) * 16
        str_size = len(self.asset_id) + len(self.market) + len(self.last_hash)

        return base + bids_size + ask_size + str_size
