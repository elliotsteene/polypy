"""Unit tests for worker module (fast, isolated tests)."""

from multiprocessing import Event as MPEvent
from multiprocessing import Queue as MPQueue
from unittest.mock import patch

import pytest

from src.messages.protocol import (
    BookSnapshot,
    EventType,
    LastTradePrice,
    ParsedMessage,
    PriceChange,
    PriceLevel,
    Side,
)
from src.orderbook.orderbook_store import Asset, OrderbookStore
from src.worker.stats import WorkerStats
from src.worker.worker import Worker, _worker_process


class TestProcessMessage:
    """Test _process_message function."""

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

    def test_process_book_message(self, mock_worker: Worker):
        """Test processing BOOK snapshot message."""

        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(
                hash="test",
                bids=(PriceLevel(price=500, size=10000),),
                asks=(PriceLevel(price=510, size=5000),),
            ),
            price_change=None,
            last_trade=None,
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        assert mock_worker._stats.snapshots_received == 1
        assert mock_worker._stats.updates_applied == 1
        assert mock_worker._store.get_state("asset1") is not None

    def test_process_price_change_message(self, mock_worker: Worker):
        """Test processing PRICE_CHANGE message."""
        # First create a book snapshot
        asset = Asset(asset_id="asset1", market="market1")
        book = mock_worker._store.register_asset(asset)
        book._bids[-500] = 10000  # 0.50 @ 100.00

        message = ParsedMessage(
            event_type=EventType.PRICE_CHANGE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=PriceChange(
                asset_id="asset1",
                price=550,  # 0.55
                size=20000,  # 200
                side=Side.BUY,
                hash="test",
                best_bid=550,
                best_ask=600,
            ),
            last_trade=None,
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        assert mock_worker._stats.updates_applied == 1

    def test_process_last_trade_message(self, mock_worker: Worker):
        """Test processing LAST_TRADE_PRICE message."""

        message = ParsedMessage(
            event_type=EventType.LAST_TRADE_PRICE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=LastTradePrice(price=500, size=10000, side=Side.BUY),
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        # Last trade doesn't update orderbook in v1
        assert mock_worker._stats.updates_applied == 0

    @patch("src.worker.worker.logger")
    def test_process_unknown_event_type(
        self,
        mock_logger,
        mock_worker: Worker,
    ):
        """Test handling of unknown event type."""
        # Create message with invalid event type
        message = ParsedMessage(
            event_type=EventType.UNKNOWN,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=None,
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        mock_logger.warning.assert_called_once()

    @patch("src.worker.worker.logger")
    def test_process_message_assertion_error(self, mock_logger, mock_worker: Worker):
        """Test error handling for assertion failures."""
        # BOOK message without book field (will fail assertion)
        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,  # Missing book!
            price_change=None,
            last_trade=None,
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        mock_logger.error.assert_called_once()

    @patch("src.worker.worker.logger")
    @patch("src.worker.worker.Worker._process_book_snapshot")
    def test_process_message_generic_exception(
        self, mock_process_book, mock_logger, mock_worker: Worker
    ):
        """Test error handling for generic exceptions."""
        # Mock processing function to raise exception
        mock_process_book.side_effect = ValueError("Test error")

        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(
                hash="test",
                bids=(),
                asks=(),
            ),
            price_change=None,
            last_trade=None,
        )

        mock_worker._process_message(message)

        assert mock_worker._stats.messages_processed == 1
        mock_logger.exception.assert_called_once()


class TestProcessBookSnapshot:
    """Test _process_book_snapshot function."""

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

    def test_process_valid_snapshot(self, mock_worker: Worker):
        """Test processing valid book snapshot."""
        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(
                hash="test",
                bids=(PriceLevel(price=500, size=10000),),
                asks=(PriceLevel(price=510, size=5000),),
            ),
            price_change=None,
            last_trade=None,
        )

        mock_worker._process_book_snapshot(message)

        assert mock_worker._stats.snapshots_received == 1
        assert mock_worker._stats.updates_applied == 1

        book = mock_worker._store.get_state("asset1")
        assert book is not None
        assert book.best_bid == 500
        assert book.best_ask == 510

    def test_process_snapshot_missing_book_field(self, mock_worker: Worker):
        """Test assertion when book field is None."""
        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,  # Missing!
            price_change=None,
            last_trade=None,
        )

        with pytest.raises(AssertionError, match="BOOK message missing book field"):
            mock_worker._process_book_snapshot(message)


class TestProcessPriceChange:
    """Test _process_price_change function."""

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

    def test_process_price_change_existing_book(self, mock_worker: Worker):
        """Test price change for existing orderbook."""
        # Create initial book
        asset = Asset(asset_id="asset1", market="market1")
        book = mock_worker._store.register_asset(asset)
        book._bids[-500] = 10000

        message = ParsedMessage(
            event_type=EventType.PRICE_CHANGE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=PriceChange(
                asset_id="asset1",
                price=550,  # 0.55
                size=20000,  # 200
                side=Side.BUY,
                hash="test",
                best_bid=550,
                best_ask=600,
            ),
            last_trade=None,
        )

        mock_worker._process_price_change(message)

        assert mock_worker._stats.updates_applied == 1
        assert -550 in book._bids

    @patch("src.worker.worker.logger")
    def test_process_price_change_unknown_asset(self, mock_logger, mock_worker: Worker):
        """Test price change for unknown asset (skip silently)."""
        message = ParsedMessage(
            event_type=EventType.PRICE_CHANGE,
            asset_id="unknown_asset",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=PriceChange(
                asset_id="unknown_asset",
                price=550,
                size=20000,
                side=Side.BUY,
                hash="test",
                best_bid=550,
                best_ask=600,
            ),
            last_trade=None,
        )

        mock_worker._process_price_change(message)

        assert mock_worker._stats.updates_applied == 0
        mock_logger.warning.assert_called_once()

    def test_process_price_change_missing_field(self, mock_worker: Worker):
        """Test assertion when price_change field is None."""
        message = ParsedMessage(
            event_type=EventType.PRICE_CHANGE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,  # Missing!
            last_trade=None,
        )

        with pytest.raises(
            AssertionError, match="PRICE_CHANGE message missing price_change field"
        ):
            mock_worker._process_price_change(message)


class TestProcessLastTrade:
    """Test _process_last_trade function."""

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

    def test_process_last_trade(self, mock_worker: Worker):
        """Test last trade processing (no-op in v1)."""
        message = ParsedMessage(
            event_type=EventType.LAST_TRADE_PRICE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=LastTradePrice(price=500, size=10000, side=Side.BUY),
        )

        # Should not raise
        mock_worker._process_last_trade(message)

    def test_process_last_trade_missing_field(self, mock_worker: Worker):
        """Test assertion when last_trade field is None."""
        message = ParsedMessage(
            event_type=EventType.LAST_TRADE_PRICE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=None,  # Missing!
        )

        with pytest.raises(
            AssertionError, match="LAST_TRADE_PRICE message missing last_trade field"
        ):
            mock_worker._process_last_trade(message)


class TestWorkerProcess:
    """Test _worker_process function (worker entry point)."""

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_shutdown_sentinel(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker shuts down when receiving None sentinel."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        # Put None sentinel
        input_queue.put(None)

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Verify graceful shutdown
        mock_logger.info.assert_any_call("Worker 0 received shutdown sentinel")

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.QUEUE_TIMEOUT", 0.01)  # Speed up test
    def test_worker_process_shutdown_event(self, mock_monotonic, mock_signal):
        """Test worker shuts down when shutdown_event is set."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        # Set shutdown event immediately
        shutdown_event.set()

        # Run worker (should exit immediately)
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Worker should have exited cleanly
        assert shutdown_event.is_set()

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_processes_message(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker processes messages correctly."""

        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        # Create test message
        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(
                hash="test",
                bids=(PriceLevel(price=500, size=10000),),
                asks=(PriceLevel(price=510, size=5000),),
            ),
            price_change=None,
            last_trade=None,
        )

        # Put message and then sentinel
        input_queue.put((message, 100.0))
        input_queue.put(None)

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Verify message was processed (check final stats)
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert worker_id == 0
        assert stats.messages_processed >= 1

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_handles_queue_timeout(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker handles Empty exception from queue timeout."""

        # Setup with short timeout
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        # Mock monotonic to simulate time passing, then trigger shutdown
        time_values = [100.0, 100.05, 100.1, 100.15]  # Simulate timeouts
        mock_monotonic.side_effect = time_values

        # Set shutdown after a few timeouts
        def delayed_shutdown():
            if mock_monotonic.call_count > 6:
                shutdown_event.set()

        mock_monotonic.side_effect = lambda: (
            delayed_shutdown() or time_values[min(mock_monotonic.call_count, 3)]
        )

        # Queue is empty, will timeout repeatedly
        input_queue.put(None)  # Eventually get sentinel

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Should have handled timeouts gracefully (no crash)
        assert True

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    @patch("src.worker.worker.HEARTBEAT_INTERVAL", 0.1)
    def test_worker_process_heartbeat_updates(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker updates stats during heartbeat interval."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        # Simulate time progression to trigger heartbeat
        # Need many values because time.monotonic is also called by queue.get() internally
        time_counter = [100.0]

        def increment_time():
            time_counter[0] += 0.01
            return time_counter[0]

        mock_monotonic.side_effect = increment_time

        # Create message to process (creates orderbook)
        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(hash="test", bids=(), asks=()),
            price_change=None,
            last_trade=None,
        )

        input_queue.put((message, 100.0))
        input_queue.put(None)  # Sentinel to exit

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Verify final stats include orderbook count and memory usage
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert stats.orderbook_count >= 0
        assert stats.memory_usage_bytes >= 0

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    @patch("src.worker.worker.STATS_INTERVAL", 0.1)
    def test_worker_process_stats_reporting(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker sends periodic stats reports."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        # Simulate time to trigger stats report
        time_sequence = [100.0, 100.05, 100.15]  # Cross stats threshold
        mock_monotonic.side_effect = time_sequence + [100.2] * 10

        input_queue.put(None)  # Immediate shutdown after stats

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Should have at least final stats report
        final_stats = stats_queue.get(timeout=1.0)
        assert final_stats is not None

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_handles_processing_exception(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker continues after processing exception."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        # Create malformed message (will cause exception)
        bad_message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,  # Missing book data!
            price_change=None,
            last_trade=None,
        )

        # Put bad message then good sentinel
        input_queue.put((bad_message, 100.0))
        input_queue.put(None)

        # Run worker - should not crash
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Worker should have logged error but continued
        mock_logger.error.assert_called()

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_finally_sends_stats(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker sends final stats in finally block."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        input_queue.put(None)

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Final stats should be in queue
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert worker_id == 0
        assert isinstance(stats, WorkerStats)

        # Final log message
        calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("stopping" in str(call).lower() for call in calls)

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_finally_handles_full_queue(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker handles Full exception in finally block."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue(maxsize=1)  # Very small queue
        shutdown_event = MPEvent()
        mock_monotonic.return_value = 100.0

        # Fill the stats queue
        stats_queue.put(("dummy", WorkerStats()))

        input_queue.put(None)

        # Run worker - should handle Full exception gracefully
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Should not crash, even though stats queue was full
        assert True

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    @patch("src.worker.worker.STATS_INTERVAL", 0.1)
    def test_worker_process_stats_report_handles_full_queue(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker handles Full exception during periodic stats reporting."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue(maxsize=1)
        shutdown_event = MPEvent()

        # Simulate time crossing stats interval
        time_sequence = [100.0, 100.05, 100.15]  # Triggers stats report
        mock_monotonic.side_effect = time_sequence + [100.2] * 10

        # Fill stats queue
        stats_queue.put(("dummy", WorkerStats()))

        input_queue.put(None)

        # Run worker - should handle Full exception during stats report
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Should not crash
        assert True

    @patch("src.worker.worker.signal.signal")
    @patch("src.worker.worker.time.monotonic")
    @patch("src.worker.worker.logger")
    def test_worker_process_tracks_processing_time(
        self, mock_logger, mock_monotonic, mock_signal
    ):
        """Test worker tracks processing time correctly."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        # Use a function that increments time to handle all time.monotonic() calls
        time_counter = [100.0]

        def increment_time():
            time_counter[0] += 0.001  # Small increment
            return time_counter[0]

        mock_monotonic.side_effect = increment_time

        message = ParsedMessage(
            event_type=EventType.BOOK,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=BookSnapshot(hash="test", bids=(), asks=()),
            price_change=None,
            last_trade=None,
        )

        input_queue.put((message, 100.0))
        input_queue.put(None)

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Verify stats tracked processing time
        final_stats = stats_queue.get(timeout=1.0)
        _, stats = final_stats
        assert stats.processing_time_ms > 0

    @patch("src.worker.worker.signal.signal")
    def test_worker_process_signal_handling_setup(self, mock_signal):
        """Test worker sets up SIGINT signal handling."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        input_queue.put(None)  # Immediate shutdown

        # Run worker
        response_queue = MPQueue()
        _worker_process(0, input_queue, stats_queue, response_queue, shutdown_event)

        # Verify signal was set to ignore SIGINT
        import signal

        mock_signal.assert_called_with(signal.SIGINT, signal.SIG_IGN)
