"""Tests for orderbook state management."""

from src.messages.protocol import BookSnapshot, PriceChange, PriceLevel, Side
from src.orderbook.orderbook import OrderbookState


class TestOrderbookState:
    """Test individual orderbook state operations."""

    def test_apply_snapshot_initializes_book(
        self, sample_book_snapshot: BookSnapshot
    ) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")

        # Act
        book.apply_snapshot(sample_book_snapshot, timestamp=1000)

        # Assert
        assert book.best_bid == 490  # Highest bid
        assert book.best_ask == 510  # Lowest ask
        assert book.last_hash == "0xabc123"
        assert book.last_update_ts == 1000

    def test_apply_snapshot_clears_existing_state(
        self, sample_book_snapshot: BookSnapshot
    ) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        # Apply initial state
        initial_snapshot = BookSnapshot(
            hash="0xold",
            bids=(PriceLevel(price=100, size=1000),),
            asks=(PriceLevel(price=200, size=2000),),
        )
        book.apply_snapshot(initial_snapshot, timestamp=1000)

        # Act - apply new snapshot
        book.apply_snapshot(sample_book_snapshot, timestamp=2000)

        # Assert - old state completely replaced
        assert book.best_bid == 490
        assert book.best_ask == 510
        assert book.last_hash == "0xabc123"

    def test_apply_price_change_buy_adds_level(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0", bids=(PriceLevel(price=490, size=1000),), asks=()
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        change = PriceChange(
            asset_id="123",
            price=500,  # New higher bid
            size=2000,
            side=Side.BUY,
            hash="0x1",
            best_bid=500,
            best_ask=510,
        )

        # Act
        book.apply_price_change(change, timestamp=2000)

        # Assert
        assert book.best_bid == 500
        bids = book.get_bids(depth=5)
        assert len(bids) == 2
        assert bids[0].price == 500  # New level first

    def test_apply_price_change_removes_level_when_size_zero(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000), PriceLevel(price=480, size=500)),
            asks=(),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        change = PriceChange(
            asset_id="123",
            price=490,
            size=0,  # Remove this level
            side=Side.BUY,
            hash="0x1",
            best_bid=480,
            best_ask=510,
        )

        # Act
        book.apply_price_change(change, timestamp=2000)

        # Assert
        assert book.best_bid == 480
        bids = book.get_bids(depth=5)
        assert len(bids) == 1

    def test_apply_price_change_sell_adds_level(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0", bids=(), asks=(PriceLevel(price=510, size=1000),)
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        change = PriceChange(
            asset_id="123",
            price=500,  # New lower ask
            size=2000,
            side=Side.SELL,
            hash="0x1",
            best_bid=490,
            best_ask=500,
        )

        # Act
        book.apply_price_change(change, timestamp=2000)

        # Assert
        assert book.best_ask == 500
        asks = book.get_asks(depth=5)
        assert len(asks) == 2
        assert asks[0].price == 500  # New level first (lowest)

    def test_best_bid_ask_cached_after_price_change(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        change = PriceChange(
            asset_id="123",
            price=495,
            size=500,
            side=Side.BUY,
            hash="0x1",
            best_bid=495,
            best_ask=510,
        )

        # Act
        book.apply_price_change(change, timestamp=2000)

        # Assert - cache should be valid
        assert book._cache_valid is True
        assert book.best_bid == 495
        assert book.best_ask == 510

    def test_spread_calculation(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act
        spread = book.spread

        # Assert
        assert spread == 20  # 510 - 490

    def test_mid_price_calculation(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act
        mid = book.mid_price

        # Assert
        assert mid == 500  # (490 + 510) // 2

    def test_get_bids_respects_depth(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        bids = tuple(PriceLevel(price=500 - i * 10, size=1000) for i in range(5))
        snapshot = BookSnapshot(hash="0x0", bids=bids, asks=())
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act
        top_2 = book.get_bids(depth=2)

        # Assert
        assert len(top_2) == 2
        assert top_2[0].price == 500  # Highest
        assert top_2[1].price == 490

    def test_get_asks_respects_depth(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        asks = tuple(PriceLevel(price=500 + i * 10, size=1000) for i in range(5))
        snapshot = BookSnapshot(hash="0x0", bids=(), asks=asks)
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act
        top_2 = book.get_asks(depth=2)

        # Assert
        assert len(top_2) == 2
        assert top_2[0].price == 500  # Lowest
        assert top_2[1].price == 510

    def test_empty_book_properties(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")

        # Act & Assert
        assert book.best_bid is None
        assert book.best_ask is None
        assert book.spread is None
        assert book.mid_price is None


class TestOrderbookStore:
    """Test multi-book registry management."""

    def test_register_asset_creates_new_state(self) -> None:
        # Arrange
        from src.orderbook.orderbook_store import Asset, OrderbookStore

        store = OrderbookStore()
        asset = Asset(asset_id="123", market="0xabc")

        # Act
        state = store.register_asset(asset)

        # Assert
        assert state is not None
        assert state.asset_id == "123"
        assert state.market == "0xabc"

    def test_register_asset_returns_existing_state(self) -> None:
        # Arrange
        from src.orderbook.orderbook_store import Asset, OrderbookStore

        store = OrderbookStore()
        asset = Asset(asset_id="123", market="0xabc")
        first_state = store.register_asset(asset)

        # Act
        second_state = store.register_asset(asset)

        # Assert
        assert second_state is first_state  # Same object

    def test_get_state_returns_none_for_unknown_asset(self) -> None:
        # Arrange
        from src.orderbook.orderbook_store import OrderbookStore

        store = OrderbookStore()

        # Act
        state = store.get_state("nonexistent")

        # Assert
        assert state is None

    def test_remove_state_removes_from_books(self) -> None:
        # Arrange
        from src.orderbook.orderbook_store import Asset, OrderbookStore

        store = OrderbookStore()
        asset = Asset(asset_id="123", market="0xabc")
        store.register_asset(asset)

        # Act
        removed = store.remove_state("123")

        # Assert
        assert removed is True
        assert store.get_state("123") is None

    def test_remove_state_updates_market_index(self) -> None:
        # Arrange
        from src.orderbook.orderbook_store import Asset, OrderbookStore

        store = OrderbookStore()
        asset1 = Asset(asset_id="123", market="0xabc")
        asset2 = Asset(asset_id="456", market="0xabc")
        store.register_asset(asset1)
        store.register_asset(asset2)

        # Act
        store.remove_state("123")

        # Assert - market index should still have asset2
        assert store.get_state("456") is not None


class TestOrderbookHistory:
    """Test historical snapshot capture and retrieval."""

    def test_snapshot_captured_on_apply_snapshot(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=2000),),
        )

        # Act
        book.apply_snapshot(snapshot, timestamp=1000)

        # Assert
        history = book.get_history()
        assert len(history) == 1
        assert history[0].timestamp == 1000
        assert history[0].best_bid == 490
        assert history[0].best_ask == 510
        assert history[0].spread == 20
        assert history[0].mid_price == 500
        assert history[0].bid_depth == 1000
        assert history[0].ask_depth == 2000

    def test_snapshot_captured_on_apply_price_change(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc", _history_interval_ms=1000)
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act - apply price change after interval
        change = PriceChange(
            asset_id="123",
            price=495,
            size=500,
            side=Side.BUY,
            hash="0x1",
            best_bid=495,
            best_ask=510,
        )
        book.apply_price_change(change, timestamp=2000)

        # Assert
        history = book.get_history()
        assert len(history) == 2
        assert history[0].timestamp == 2000  # Newest first
        assert history[0].best_bid == 495
        assert history[1].timestamp == 1000

    def test_snapshot_not_captured_if_interval_not_elapsed(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act - apply change within interval (< 1000ms)
        change = PriceChange(
            asset_id="123",
            price=495,
            size=500,
            side=Side.BUY,
            hash="0x1",
            best_bid=495,
            best_ask=510,
        )
        book.apply_price_change(change, timestamp=1500)  # Only 500ms later

        # Assert - should still only have 1 snapshot
        history = book.get_history()
        assert len(history) == 1
        assert history[0].timestamp == 1000

    def test_history_ring_buffer_maxlen(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc", _history_interval_ms=1000)
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )

        # Act - apply 101 snapshots (buffer maxlen=100)
        for i in range(101):
            book.apply_snapshot(snapshot, timestamp=i * 1000)

        # Assert - should only keep most recent 100
        history = book.get_history()
        assert len(history) == 100
        assert history[0].timestamp == 100000  # Newest
        assert history[-1].timestamp == 1000  # Oldest retained

    def test_get_history_limit_parameter(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc", _history_interval_ms=1000)
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )

        # Apply 10 snapshots
        for i in range(10):
            book.apply_snapshot(snapshot, timestamp=i * 1000)

        # Act
        history = book.get_history(limit=5)

        # Assert - should return only 5 newest
        assert len(history) == 5
        assert history[0].timestamp == 9000  # Newest
        assert history[-1].timestamp == 5000

    def test_get_history_empty_book(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")

        # Act
        history = book.get_history()

        # Assert
        assert len(history) == 0

    def test_orderbook_snapshot_immutable(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Act
        history = book.get_history()
        first_snapshot = history[0]

        # Assert - snapshot is frozen (immutable)
        try:
            first_snapshot.timestamp = 2000  # type: ignore
            assert False, "Should not allow mutation"
        except AttributeError:
            pass  # Expected - dataclass is frozen

    def test_snapshot_captures_depth_correctly(self) -> None:
        # Arrange
        book = OrderbookState(asset_id="123", market="0xabc")
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(
                PriceLevel(price=490, size=1000),
                PriceLevel(price=480, size=500),
                PriceLevel(price=470, size=300),
            ),
            asks=(
                PriceLevel(price=510, size=2000),
                PriceLevel(price=520, size=1500),
            ),
        )

        # Act
        book.apply_snapshot(snapshot, timestamp=1000)

        # Assert
        history = book.get_history()
        assert len(history) == 1
        assert history[0].bid_depth == 1800  # 1000 + 500 + 300
        assert history[0].ask_depth == 3500  # 2000 + 1500
