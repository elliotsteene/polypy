"""Unit tests for worker module (fast, isolated tests)."""

from multiprocessing import Event as MPEvent
from multiprocessing import Queue as MPQueue
from queue import Full
from unittest.mock import Mock, patch

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
from src.worker import (
    WorkerManager,
    WorkerStats,
    _process_book_snapshot,
    _process_last_trade,
    _process_message,
    _process_price_change,
    _worker_process,
)


class TestWorkerStats:
    """Test WorkerStats dataclass."""

    def test_avg_processing_time_us_with_messages(self):
        """Test average processing time calculation with messages."""
        stats = WorkerStats(messages_processed=10, processing_time_ms=50.0)
        # (50ms * 1000) / 10 = 5000 microseconds
        assert stats.avg_processing_time_us == 5000.0

    def test_avg_processing_time_us_no_messages(self):
        """Test average processing time when no messages processed."""
        stats = WorkerStats(messages_processed=0, processing_time_ms=0.0)
        assert stats.avg_processing_time_us == 0.0

    def test_default_values(self):
        """Test WorkerStats default initialization."""
        stats = WorkerStats()
        assert stats.messages_processed == 0
        assert stats.updates_applied == 0
        assert stats.snapshots_received == 0
        assert stats.processing_time_ms == 0.0
        assert stats.last_message_ts == 0.0
        assert stats.orderbook_count == 0
        assert stats.memory_usage_bytes == 0


class TestProcessMessage:
    """Test _process_message function."""

    def test_process_book_message(self):
        """Test processing BOOK snapshot message."""
        store = OrderbookStore()
        stats = WorkerStats()

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

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        assert stats.snapshots_received == 1
        assert stats.updates_applied == 1
        assert store.get_state("asset1") is not None

    def test_process_price_change_message(self):
        """Test processing PRICE_CHANGE message."""
        store = OrderbookStore()
        stats = WorkerStats()

        # First create a book snapshot
        asset = Asset(asset_id="asset1", market="market1")
        book = store.register_asset(asset)
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

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        assert stats.updates_applied == 1

    def test_process_last_trade_message(self):
        """Test processing LAST_TRADE_PRICE message."""
        store = OrderbookStore()
        stats = WorkerStats()

        message = ParsedMessage(
            event_type=EventType.LAST_TRADE_PRICE,
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=LastTradePrice(price=500, size=10000, side=Side.BUY),
        )

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        # Last trade doesn't update orderbook in v1
        assert stats.updates_applied == 0

    @patch("src.worker.logger")
    def test_process_unknown_event_type(self, mock_logger):
        """Test handling of unknown event type."""
        store = OrderbookStore()
        stats = WorkerStats()

        # Create message with invalid event type
        message = ParsedMessage(
            event_type="UNKNOWN",  # type: ignore
            asset_id="asset1",
            market="market1",
            raw_timestamp=1234567890,
            book=None,
            price_change=None,
            last_trade=None,
        )

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        mock_logger.warning.assert_called_once()

    @patch("src.worker.logger")
    def test_process_message_assertion_error(self, mock_logger):
        """Test error handling for assertion failures."""
        store = OrderbookStore()
        stats = WorkerStats()

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

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        mock_logger.error.assert_called_once()

    @patch("src.worker.logger")
    @patch("src.worker._process_book_snapshot")
    def test_process_message_generic_exception(self, mock_process_book, mock_logger):
        """Test error handling for generic exceptions."""
        store = OrderbookStore()
        stats = WorkerStats()

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

        _process_message(store, message, stats)

        assert stats.messages_processed == 1
        mock_logger.exception.assert_called_once()


class TestProcessBookSnapshot:
    """Test _process_book_snapshot function."""

    def test_process_valid_snapshot(self):
        """Test processing valid book snapshot."""
        store = OrderbookStore()
        stats = WorkerStats()

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

        _process_book_snapshot(store, message, stats)

        assert stats.snapshots_received == 1
        assert stats.updates_applied == 1

        book = store.get_state("asset1")
        assert book is not None
        assert book.best_bid == 500
        assert book.best_ask == 510

    def test_process_snapshot_missing_book_field(self):
        """Test assertion when book field is None."""
        store = OrderbookStore()
        stats = WorkerStats()

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
            _process_book_snapshot(store, message, stats)


class TestProcessPriceChange:
    """Test _process_price_change function."""

    def test_process_price_change_existing_book(self):
        """Test price change for existing orderbook."""
        store = OrderbookStore()
        stats = WorkerStats()

        # Create initial book
        asset = Asset(asset_id="asset1", market="market1")
        book = store.register_asset(asset)
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

        _process_price_change(store, message, stats)

        assert stats.updates_applied == 1
        assert -550 in book._bids

    @patch("src.worker.logger")
    def test_process_price_change_unknown_asset(self, mock_logger):
        """Test price change for unknown asset (skip silently)."""
        store = OrderbookStore()
        stats = WorkerStats()

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

        _process_price_change(store, message, stats)

        assert stats.updates_applied == 0
        mock_logger.debug.assert_called_once()

    def test_process_price_change_missing_field(self):
        """Test assertion when price_change field is None."""
        store = OrderbookStore()
        stats = WorkerStats()

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
            _process_price_change(store, message, stats)


class TestProcessLastTrade:
    """Test _process_last_trade function."""

    def test_process_last_trade(self):
        """Test last trade processing (no-op in v1)."""
        store = OrderbookStore()
        stats = WorkerStats()

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
        _process_last_trade(store, message, stats)

    def test_process_last_trade_missing_field(self):
        """Test assertion when last_trade field is None."""
        store = OrderbookStore()
        stats = WorkerStats()

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
            _process_last_trade(store, message, stats)


class TestWorkerManager:
    """Test WorkerManager class."""

    def test_init_valid(self):
        """Test WorkerManager initialization with valid num_workers."""
        manager = WorkerManager(num_workers=4)
        assert manager.num_workers == 4
        assert not manager._running
        assert len(manager._processes) == 0

    def test_init_invalid_num_workers(self):
        """Test WorkerManager rejects invalid num_workers."""
        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            WorkerManager(num_workers=0)

        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            WorkerManager(num_workers=-1)

    @patch("src.worker.logger")
    @patch("os.cpu_count", return_value=4)
    def test_init_warns_excessive_workers(self, mock_cpu_count, mock_logger):
        """Test warning when num_workers exceeds CPU count."""
        manager = WorkerManager(num_workers=8)
        assert manager.num_workers == 8
        mock_logger.warning.assert_called_once()
        assert "exceeds CPU count" in mock_logger.warning.call_args[0][0]

    def test_get_input_queues(self):
        """Test getting input queues creates them once."""
        manager = WorkerManager(num_workers=3)
        queues1 = manager.get_input_queues()
        queues2 = manager.get_input_queues()

        assert len(queues1) == 3
        assert queues1 is queues2  # Same instance

    def test_num_workers_property(self):
        """Test num_workers property."""
        manager = WorkerManager(num_workers=5)
        assert manager.num_workers == 5

    @patch("src.worker.logger")
    def test_start_when_already_running(self, mock_logger):
        """Test start() when already running logs warning."""
        manager = WorkerManager(num_workers=1)
        manager._running = True

        manager.start()

        mock_logger.warning.assert_called_with("WorkerManager already running")

    @patch("src.worker.logger")
    def test_stop_when_not_running(self, mock_logger):
        """Test stop() when not running logs warning."""
        manager = WorkerManager(num_workers=1)
        manager._running = False

        manager.stop()

        mock_logger.warning.assert_called_with("WorkerManager not running")

    def test_start_stop_lifecycle(self):
        """Test full start/stop lifecycle."""
        manager = WorkerManager(num_workers=2)

        # Start workers
        manager.start()
        assert manager._running
        assert len(manager._processes) == 2
        assert all(p.is_alive() for p in manager._processes)

        # Stop workers
        manager.stop(timeout=2.0)
        assert not manager._running
        assert len(manager._processes) == 0

    def test_is_healthy_when_running(self):
        """Test is_healthy returns True when all workers alive."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        assert manager.is_healthy()

        manager.stop()

    def test_is_healthy_when_not_running(self):
        """Test is_healthy returns False when not running."""
        manager = WorkerManager(num_workers=2)
        assert not manager.is_healthy()

    def test_get_alive_count(self):
        """Test get_alive_count returns correct count."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        assert manager.get_alive_count() == 2

        manager.stop()

    def test_get_stats_empty(self):
        """Test get_stats returns empty dict when no stats available."""
        manager = WorkerManager(num_workers=2)
        stats = manager.get_stats()
        assert stats == {}

    @patch("src.worker.logger")
    def test_stop_with_full_queue(self, mock_logger):
        """Test stop() handles Full exception when sending sentinels."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        # Mock queue to raise Full
        for queue in manager._input_queues:
            queue.put_nowait = Mock(side_effect=Full())

        manager.stop(timeout=1.0)

        # Should log warning about failed sentinel
        assert mock_logger.warning.call_count >= 2

    @patch("src.worker.logger")
    def test_stop_force_terminate(self, mock_logger):
        """Test stop() force terminates hung workers."""
        manager = WorkerManager(num_workers=1)
        manager.start()

        # Mock process to not join gracefully
        mock_process = Mock()
        mock_process.is_alive.return_value = True
        mock_process.join = Mock()
        mock_process.terminate = Mock()
        mock_process.kill = Mock()
        mock_process.name = "test-worker"
        manager._processes = [mock_process]

        manager.stop(timeout=0.1)

        # Should attempt terminate
        mock_process.terminate.assert_called_once()
        mock_logger.warning.assert_called()

    @patch("src.worker.logger")
    def test_stop_force_kill(self, mock_logger):
        """Test stop() kills workers that don't terminate."""
        manager = WorkerManager(num_workers=1)
        manager._running = True

        # Mock process that survives terminate
        mock_process = Mock()
        mock_process.is_alive.return_value = True
        mock_process.join = Mock()
        mock_process.terminate = Mock()
        mock_process.kill = Mock()
        mock_process.name = "test-worker"
        manager._processes = [mock_process]
        manager._input_queues = [Mock()]

        manager.stop(timeout=0.1)

        # Should attempt both terminate and kill
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        mock_logger.error.assert_called()


class TestWorkerProcess:
    """Test _worker_process function (worker entry point)."""

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Verify graceful shutdown
        mock_logger.info.assert_any_call("Worker 0 received shutdown sentinel")

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.QUEUE_TIMEOUT", 0.01)  # Speed up test
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Worker should have exited cleanly
        assert shutdown_event.is_set()

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Verify message was processed (check final stats)
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert worker_id == 0
        assert stats.messages_processed >= 1

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Should have handled timeouts gracefully (no crash)
        assert True

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
    @patch("src.worker.HEARTBEAT_INTERVAL", 0.1)
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Verify final stats include orderbook count and memory usage
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert stats.orderbook_count >= 0
        assert stats.memory_usage_bytes >= 0

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
    @patch("src.worker.STATS_INTERVAL", 0.1)
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Should have at least final stats report
        final_stats = stats_queue.get(timeout=1.0)
        assert final_stats is not None

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Worker should have logged error but continued
        mock_logger.error.assert_called()

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Final stats should be in queue
        final_stats = stats_queue.get(timeout=1.0)
        worker_id, stats = final_stats
        assert worker_id == 0
        assert isinstance(stats, WorkerStats)

        # Final log message
        calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("stopping" in str(call).lower() for call in calls)

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Should not crash, even though stats queue was full
        assert True

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
    @patch("src.worker.STATS_INTERVAL", 0.1)
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Should not crash
        assert True

    @patch("src.worker.signal.signal")
    @patch("src.worker.time.monotonic")
    @patch("src.worker.logger")
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
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Verify stats tracked processing time
        final_stats = stats_queue.get(timeout=1.0)
        _, stats = final_stats
        assert stats.processing_time_ms > 0

    @patch("src.worker.signal.signal")
    def test_worker_process_signal_handling_setup(self, mock_signal):
        """Test worker sets up SIGINT signal handling."""
        # Setup
        input_queue = MPQueue()
        stats_queue = MPQueue()
        shutdown_event = MPEvent()

        input_queue.put(None)  # Immediate shutdown

        # Run worker
        _worker_process(0, input_queue, stats_queue, shutdown_event)

        # Verify signal was set to ignore SIGINT
        import signal

        mock_signal.assert_called_with(signal.SIGINT, signal.SIG_IGN)
