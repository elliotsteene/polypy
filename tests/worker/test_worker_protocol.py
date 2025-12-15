from multiprocessing import Queue as MPQueue

import pytest

from src.messages.protocol import (
    BookSnapshot,
    PriceLevel,
)
from src.orderbook.orderbook_store import Asset, OrderbookStore
from src.worker.protocol import OrderbookRequest
from src.worker.stats import WorkerStats
from src.worker.worker import Worker


class TestHandleOrderbookRequest:
    """Test _handle_orderbook_request function."""

    @pytest.fixture
    def mock_worker(self) -> Worker:
        store = OrderbookStore()
        stats = WorkerStats()
        input_queue = MPQueue()
        stats_queue = MPQueue()
        response_queue = MPQueue()

        return Worker(
            worker_id=1,
            input_queue=input_queue,
            stats_queue=stats_queue,
            response_queue=response_queue,
            orderbook_store=store,
            stats=stats,
        )

    def test_orderbook_request_asset_not_found(self, mock_worker: Worker):
        """Test orderbook request for non-existent asset."""
        request = OrderbookRequest(
            request_id="req123", asset_id="nonexistent", depth=10
        )

        mock_worker._handle_orderbook_request(request)

        # Check response queue (use timeout for multiprocessing queue sync)
        response = mock_worker._response_queue.get(timeout=1.0)
        assert response.request_id == "req123"
        assert response.asset_id == "nonexistent"
        assert response.found is False
        assert response.error == "Asset not found"
        assert len(response.history) == 0

    def test_orderbook_request_returns_history(self, mock_worker: Worker):
        """Test orderbook request includes history data."""
        # Create orderbook with history
        asset = Asset(asset_id="asset1", market="market1")
        book = mock_worker._store.register_asset(asset)
        book._history_interval_ms = 1000

        # Apply snapshots to build history
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=2000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Apply another snapshot after interval
        snapshot2 = BookSnapshot(
            hash="0x1",
            bids=(PriceLevel(price=495, size=1500),),
            asks=(PriceLevel(price=505, size=1800),),
        )
        book.apply_snapshot(snapshot2, timestamp=2000)

        # Make request
        request = OrderbookRequest(request_id="req123", asset_id="asset1", depth=10)
        mock_worker._handle_orderbook_request(request)

        # Check response (use timeout for multiprocessing queue sync)
        response = mock_worker._response_queue.get(timeout=1.0)
        assert response.found is True
        assert len(response.history) == 2

        # Verify history is newest first
        assert response.history[0].timestamp == 2000
        assert response.history[0].best_bid == 495
        assert response.history[0].best_ask == 505
        assert response.history[0].spread == 10
        assert response.history[0].mid_price == 500

        assert response.history[1].timestamp == 1000
        assert response.history[1].best_bid == 490
        assert response.history[1].best_ask == 510

    def test_orderbook_request_history_limit(self, mock_worker: Worker):
        """Test orderbook request respects history limit of 50."""
        # Create orderbook with lots of history
        asset = Asset(asset_id="asset1", market="market1")
        book = mock_worker._store.register_asset(asset)
        book._history_interval_ms = 1000

        # Apply 60 snapshots
        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=1000),),
        )
        for i in range(60):
            book.apply_snapshot(snapshot, timestamp=i * 1000)

        # Make request
        request = OrderbookRequest(request_id="req123", asset_id="asset1", depth=10)
        mock_worker._handle_orderbook_request(request)

        # Check response (use timeout for multiprocessing queue sync)
        response = mock_worker._response_queue.get(timeout=1.0)
        assert response.found is True
        assert len(response.history) == 50  # Limited to 50

        # Verify newest first
        assert response.history[0].timestamp == 59000
        assert response.history[-1].timestamp == 10000

    def test_orderbook_request_empty_history(self, mock_worker: Worker):
        """Test orderbook request with no history."""
        # Create orderbook but don't apply any snapshots
        asset = Asset(asset_id="asset1", market="market1")
        mock_worker._store.register_asset(asset)

        # Make request
        request = OrderbookRequest(request_id="req123", asset_id="asset1", depth=10)
        mock_worker._handle_orderbook_request(request)

        # Check response (use timeout for multiprocessing queue sync)
        response = mock_worker._response_queue.get(timeout=1.0)
        assert response.found is True
        assert len(response.history) == 0

    def test_orderbook_request_history_dataclass_structure(self, mock_worker: Worker):
        """Test history points have correct structure."""
        # Create orderbook with history
        asset = Asset(asset_id="asset1", market="market1")
        book = mock_worker._store.register_asset(asset)

        snapshot = BookSnapshot(
            hash="0x0",
            bids=(PriceLevel(price=490, size=1000),),
            asks=(PriceLevel(price=510, size=2000),),
        )
        book.apply_snapshot(snapshot, timestamp=1000)

        # Make request
        request = OrderbookRequest(request_id="req123", asset_id="asset1", depth=10)
        mock_worker._handle_orderbook_request(request)

        # Check response (use timeout for multiprocessing queue sync)
        response = mock_worker._response_queue.get(timeout=1.0)
        history_point = response.history[0]

        # Verify HistoryPoint has required fields
        assert hasattr(history_point, "timestamp")
        assert hasattr(history_point, "best_bid")
        assert hasattr(history_point, "best_ask")
        assert hasattr(history_point, "spread")
        assert hasattr(history_point, "mid_price")

        # Verify values are correct types
        assert isinstance(history_point.timestamp, int)
        assert isinstance(history_point.best_bid, int) or history_point.best_bid is None
        assert isinstance(history_point.best_ask, int) or history_point.best_ask is None
        assert isinstance(history_point.spread, int) or history_point.spread is None
        assert (
            isinstance(history_point.mid_price, int) or history_point.mid_price is None
        )
