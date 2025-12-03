from typing import NamedTuple

from src.orderbook import OrderbookState


class Asset(NamedTuple):
    asset_id: str
    market: str


class OrderbookStore:
    """
    Collection of orderbooks with efficient lookup

    Used by worker processes to manage multiple orderbook states
    """

    __slots__ = ("_books", "_by_market")

    def __init__(self) -> None:
        self._books: dict[str, OrderbookState] = {}  # asset_id -> state
        self._by_market: dict[str, list[str]] = {}  # market -> [asset_ids]

    def register_asset(self, asset: Asset) -> OrderbookState:
        """
        Get existing orderbook or create new one
        """
        orderbook_state = self.get_state(asset.asset_id)
        if not orderbook_state:
            orderbook_state = self._add_state(asset=asset)

        return orderbook_state

    def get_state(self, asset_id: str) -> OrderbookState | None:
        return self._books.get(asset_id)

    def remove_state(self, asset_id: str) -> bool:
        book = self._books.pop(asset_id, None)

        if book and book.market:
            for i, m_asset_id in enumerate(self._by_market.get(book.market, [])):
                if asset_id == m_asset_id:
                    self._by_market[book.market].pop(i)

        return book is not None

    def _add_state(self, asset: Asset) -> OrderbookState:
        state = OrderbookState(
            asset_id=asset.asset_id,
            market=asset.market,
        )
        self._books[asset.asset_id] = state

        self._add_market(asset)

        return state

    def _add_market(self, asset: Asset) -> None:
        if asset.market not in self._by_market:
            self._by_market[asset.market] = []

        curr_assets = set(self._by_market[asset.market])
        if asset.asset_id not in curr_assets:
            self._by_market[asset.market].append(asset.asset_id)

    def memory_usage(self) -> int:
        return sum(book.__sizeof__() for book in self._books.values())
