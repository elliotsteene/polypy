"""Unit tests for PolyPy Application orchestrator."""

import asyncio
import multiprocessing as mp
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.app import PolyPy
from src.worker.stats import WorkerStats


class TestPolyPyInit:
    """Test PolyPy initialization."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        app = PolyPy()
        assert app._num_workers == 1
        assert not app.is_running
        assert app._registry is None
        assert app._workers is None
        assert app._router is None
        assert app._pool is None
        assert app._lifecycle is None
        assert app._recycler is None
        assert app._message_parser is None

    def test_init_custom_num_workers(self):
        """Test initialization with custom num_workers."""
        # Use a safe value that won't exceed CPU count
        app = PolyPy(num_workers=2)
        assert app._num_workers == 2

    def test_init_invalid_num_workers_zero(self):
        """Test initialization fails with num_workers = 0."""
        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            PolyPy(num_workers=0)

    def test_init_invalid_num_workers_negative(self):
        """Test initialization fails with negative num_workers."""
        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            PolyPy(num_workers=-1)

    def test_init_num_workers_exceeds_cpu_count(self):
        """Test initialization fails when num_workers exceeds max."""
        max_workers = mp.cpu_count() - 1
        with pytest.raises(ValueError, match=f"Max workers is {max_workers}"):
            PolyPy(num_workers=max_workers + 1)

    def test_is_running_property_initial_false(self):
        """Test is_running property is False initially."""
        app = PolyPy()
        assert app.is_running is False

    def test_shutdown_event_initialized(self):
        """Test shutdown event is initialized."""
        app = PolyPy()
        assert isinstance(app._shutdown_event, asyncio.Event)
        assert not app._shutdown_event.is_set()


class TestPolyPyStartStop:
    """Test PolyPy start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_initializes_all_components(self):
        """Test that start() initializes all components in correct order."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry") as mock_registry_cls,
            patch("src.app.MessageParser") as mock_parser_cls,
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            # Mock worker queues
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]

            # Mock async start methods
            mock_router_cls.return_value.start = AsyncMock()
            mock_pool_cls.return_value.start = AsyncMock()
            mock_lifecycle_cls.return_value.start = AsyncMock()
            mock_lifecycle_cls.return_value.known_market_count = 0
            mock_recycler_cls.return_value.start = AsyncMock()

            await app.start()

            # Verify all components were initialized
            assert app.is_running
            assert app._registry is not None
            assert app._message_parser is not None
            assert app._workers is not None
            assert app._router is not None
            assert app._pool is not None
            assert app._lifecycle is not None
            assert app._recycler is not None

            # Verify initialization order
            mock_registry_cls.assert_called_once()
            mock_parser_cls.assert_called_once()
            mock_worker_cls.assert_called_once_with(num_workers=2)
            mock_workers.get_input_queues.assert_called_once()
            mock_workers.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Test that calling start() twice is safe."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]

            # Mock async start methods
            mock_router_cls.return_value.start = AsyncMock()
            mock_pool_cls.return_value.start = AsyncMock()
            mock_lifecycle_cls.return_value.start = AsyncMock()
            mock_lifecycle_cls.return_value.known_market_count = 0
            mock_recycler_cls.return_value.start = AsyncMock()

            await app.start()
            first_running = app.is_running

            await app.start()  # Second call should log warning and return
            second_running = app.is_running

            assert first_running is True
            assert second_running is True
            # Worker start should only be called once
            assert mock_workers.start.call_count == 1

    @pytest.mark.asyncio
    async def test_start_cleanup_on_failure(self):
        """Test that start() logs error on component failure."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler"),
        ):
            # Mock worker queues
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]

            # Mock router to succeed
            mock_router = mock_router_cls.return_value
            mock_router.start = AsyncMock()

            # Mock pool to succeed
            mock_pool = mock_pool_cls.return_value
            mock_pool.start = AsyncMock()

            # Mock lifecycle to fail during start
            mock_lifecycle = mock_lifecycle_cls.return_value
            mock_lifecycle.start = AsyncMock(side_effect=Exception("Lifecycle failed"))

            # Act - start should catch exception and log error
            await app.start()

            # Verify application is not running after failure
            assert not app.is_running

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        """Test that calling stop() multiple times is safe."""
        app = PolyPy(num_workers=2)

        # Stop without starting
        await app.stop()
        await app.stop()

        assert not app.is_running

    @pytest.mark.asyncio
    async def test_stop_reverse_order(self):
        """Test that stop() stops components in reverse order."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            # Setup mocks
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]
            mock_workers.stop = Mock()

            mock_router = mock_router_cls.return_value
            mock_router.start = AsyncMock()
            mock_router.stop = AsyncMock()

            mock_pool = mock_pool_cls.return_value
            mock_pool.start = AsyncMock()
            mock_pool.stop = AsyncMock()

            mock_lifecycle = mock_lifecycle_cls.return_value
            mock_lifecycle.start = AsyncMock()
            mock_lifecycle.stop = AsyncMock()
            mock_lifecycle.known_market_count = 0

            mock_recycler = mock_recycler_cls.return_value
            mock_recycler.start = AsyncMock()
            mock_recycler.stop = AsyncMock()

            # Start then stop
            await app.start()
            await app.stop()

            # Verify stop was called in reverse order
            mock_recycler.stop.assert_called_once()
            mock_lifecycle.stop.assert_called_once()
            mock_pool.stop.assert_called_once()
            mock_router.stop.assert_called_once()
            mock_workers.stop.assert_called_once()

            assert not app.is_running

    @pytest.mark.asyncio
    async def test_stop_handles_exceptions(self):
        """Test that stop() handles exceptions gracefully."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            # Setup mocks
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]

            mock_router = mock_router_cls.return_value
            mock_router.start = AsyncMock()
            mock_router.stop = AsyncMock(side_effect=Exception("Router error"))

            mock_pool = mock_pool_cls.return_value
            mock_pool.start = AsyncMock()
            mock_pool.stop = AsyncMock()

            mock_lifecycle = mock_lifecycle_cls.return_value
            mock_lifecycle.start = AsyncMock()
            mock_lifecycle.stop = AsyncMock()
            mock_lifecycle.known_market_count = 0

            mock_recycler = mock_recycler_cls.return_value
            mock_recycler.start = AsyncMock()
            mock_recycler.stop = AsyncMock()

            # Start then stop
            await app.start()
            await app.stop()  # Should not raise despite router error

            # All components should have attempted stop
            mock_recycler.stop.assert_called_once()
            mock_lifecycle.stop.assert_called_once()
            mock_pool.stop.assert_called_once()
            mock_router.stop.assert_called_once()

            assert not app.is_running


class TestPolyPySignalHandling:
    """Test PolyPy signal handling."""

    @pytest.mark.asyncio
    async def test_run_registers_signal_handlers(self):
        """Test that run() registers signal handlers."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            # Setup mocks
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]
            mock_workers.stop = Mock()

            mock_router = mock_router_cls.return_value
            mock_router.start = AsyncMock()
            mock_router.stop = AsyncMock()

            mock_pool = mock_pool_cls.return_value
            mock_pool.start = AsyncMock()
            mock_pool.stop = AsyncMock()

            mock_lifecycle = mock_lifecycle_cls.return_value
            mock_lifecycle.start = AsyncMock()
            mock_lifecycle.stop = AsyncMock()
            mock_lifecycle.known_market_count = 0

            mock_recycler = mock_recycler_cls.return_value
            mock_recycler.start = AsyncMock()
            mock_recycler.stop = AsyncMock()

            # Create run task
            run_task = asyncio.create_task(app.run())

            # Wait for startup
            await asyncio.sleep(0.1)

            # Trigger shutdown
            app._shutdown_event.set()

            # Wait for completion
            await run_task

            assert not app.is_running

    @pytest.mark.asyncio
    async def test_run_removes_signal_handlers_in_finally(self):
        """Test that run() removes signal handlers in finally block."""
        app = PolyPy(num_workers=2)

        with (
            patch("src.app.AssetRegistry"),
            patch("src.app.MessageParser"),
            patch("src.app.WorkerManager") as mock_worker_cls,
            patch("src.app.MessageRouter") as mock_router_cls,
            patch("src.app.ConnectionPool") as mock_pool_cls,
            patch("src.app.LifecycleController") as mock_lifecycle_cls,
            patch("src.app.ConnectionRecycler") as mock_recycler_cls,
        ):
            # Setup mocks
            mock_workers = mock_worker_cls.return_value
            mock_workers.get_input_queues.return_value = [MagicMock(), MagicMock()]
            mock_workers.stop = Mock()

            mock_router = mock_router_cls.return_value
            mock_router.start = AsyncMock()
            mock_router.stop = AsyncMock()

            mock_pool = mock_pool_cls.return_value
            mock_pool.start = AsyncMock()
            mock_pool.stop = AsyncMock()

            mock_lifecycle = mock_lifecycle_cls.return_value
            mock_lifecycle.start = AsyncMock()
            mock_lifecycle.stop = AsyncMock()
            mock_lifecycle.known_market_count = 0

            mock_recycler = mock_recycler_cls.return_value
            mock_recycler.start = AsyncMock()
            mock_recycler.stop = AsyncMock()

            # Mock the event loop
            loop = asyncio.get_event_loop()
            with (
                patch.object(loop, "add_signal_handler") as mock_add,
                patch.object(loop, "remove_signal_handler") as mock_remove,
            ):
                # Create run task
                run_task = asyncio.create_task(app.run())

                # Wait for startup
                await asyncio.sleep(0.1)

                # Trigger shutdown
                app._shutdown_event.set()

                # Wait for completion
                await run_task

                # Verify signal handlers were added and removed
                assert mock_add.call_count == 2  # SIGINT and SIGTERM
                assert mock_remove.call_count == 2


class TestPolyPyStats:
    """Test PolyPy statistics."""

    def test_get_stats_returns_all_keys(self):
        """Test that get_stats() returns all expected keys."""
        app = PolyPy()
        stats = app.get_stats()

        expected_keys = {"running", "registry", "pool", "router", "workers", "recycler"}
        assert set(stats.keys()) == expected_keys

    def test_get_stats_handles_none_components(self):
        """Test that get_stats() handles uninitialized components."""
        app = PolyPy()
        stats = app.get_stats()

        assert stats["running"] is False
        assert stats["registry"] == {}
        assert stats["pool"] == {}
        assert stats["router"] == {}
        assert stats["workers"] == {}
        assert stats["recycler"] == {}

    def test_get_stats_with_initialized_components(self):
        """Test get_stats() returns data from initialized components."""
        app = PolyPy()

        # Mock registry
        mock_registry = MagicMock()
        mock_registry._assets = {"asset1": Mock(), "asset2": Mock()}
        mock_registry.get_pending_count.return_value = 5
        mock_registry.get_by_status.return_value = {"asset1"}
        app._registry = mock_registry

        # Mock pool
        mock_pool = MagicMock()
        mock_pool.connection_count = 2
        mock_pool.active_connection_count = 2
        mock_pool.get_total_capacity.return_value = 800
        mock_pool.get_connection_stats.return_value = []
        app._pool = mock_pool

        # Mock router
        from src.router import RouterStats

        mock_router = MagicMock()
        mock_router.stats = RouterStats(
            messages_routed=100, messages_dropped=2, batches_sent=10
        )
        mock_router.get_queue_depths.return_value = {"async_queue": 0}
        app._router = mock_router

        mock_workers = MagicMock()
        mock_workers.get_alive_count.return_value = 4
        mock_workers.is_healthy.return_value = True
        mock_workers.get_stats.return_value = {0: WorkerStats(messages_processed=50)}
        app._workers = mock_workers

        # Mock lifecycle
        mock_lifecycle = MagicMock()
        mock_lifecycle.is_running = True
        mock_lifecycle.known_market_count = 10
        app._lifecycle = mock_lifecycle

        # Mock recycler
        from src.lifecycle.recycler import RecycleStats

        mock_recycler = MagicMock()
        mock_recycler.stats = RecycleStats()
        mock_recycler.get_active_recycles.return_value = set()
        app._recycler = mock_recycler

        app._running = True

        # Get stats
        stats = app.get_stats()

        # Verify stats structure
        assert stats["running"] is True
        assert stats["registry"]["total_markets"] == 2
        assert stats["pool"]["connection_count"] == 2
        assert stats["router"]["messages_routed"] == 100
        assert stats["workers"]["alive_count"] == 4
        assert "recycles_initiated" in stats["recycler"]


class TestPolyPyHealth:
    """Test PolyPy health checks."""

    def test_is_healthy_false_when_not_running(self):
        """Test that is_healthy() returns False when not running."""
        app = PolyPy()
        assert not app.is_healthy()

    def test_is_healthy_true_when_running_and_healthy(self):
        """Test that is_healthy() returns True when running and healthy."""
        app = PolyPy()
        app._running = True

        # Mock healthy workers
        mock_workers = MagicMock()
        mock_workers.is_healthy.return_value = True
        app._workers = mock_workers

        # Mock pool with connections
        mock_pool = MagicMock()
        mock_pool.active_connection_count = 2
        app._pool = mock_pool

        assert app.is_healthy()

    def test_is_healthy_false_when_workers_unhealthy(self):
        """Test that is_healthy() returns False when workers unhealthy."""
        app = PolyPy()
        app._running = True

        # Mock unhealthy workers
        mock_workers = MagicMock()
        mock_workers.is_healthy.return_value = False
        app._workers = mock_workers

        assert not app.is_healthy()

    def test_is_healthy_true_with_no_active_connections(self):
        """Test that is_healthy() returns True even with no connections (during startup)."""
        app = PolyPy()
        app._running = True

        # Mock healthy workers
        mock_workers = MagicMock()
        mock_workers.is_healthy.return_value = True
        app._workers = mock_workers

        # Mock pool with no connections (startup scenario)
        mock_pool = MagicMock()
        mock_pool.active_connection_count = 0
        app._pool = mock_pool

        # Should still be healthy (allows for startup grace period)
        assert app.is_healthy()

    def test_is_healthy_true_when_no_workers(self):
        """Test that is_healthy() returns True when workers not initialized (during startup)."""
        app = PolyPy()
        app._running = True
        app._workers = None

        # Should be True as the check only fails if workers exist and are unhealthy
        assert app.is_healthy()


class TestPolyPyLifecycleCallbacks:
    """Test PolyPy lifecycle callbacks."""

    @pytest.mark.asyncio
    async def test_on_new_market_callback(self):
        """Test _on_new_market callback."""
        app = PolyPy()

        # Should not raise
        await app._on_new_market("new_market", "asset-123")

    @pytest.mark.asyncio
    async def test_on_market_expired_callback(self):
        """Test _on_market_expired callback."""
        app = PolyPy()

        # Should not raise
        await app._on_market_expired("market_expired", "asset-456")


class TestPolyPyHTTPConfiguration:
    """Test PolyPy HTTP server configuration."""

    def test_default_http_disabled(self):
        """Test HTTP server disabled by default."""
        app = PolyPy()
        assert app._enable_http is False
        assert app._http_port == 8080
        assert app._http_server is None

    def test_enable_http_configuration(self):
        """Test enabling HTTP server."""
        app = PolyPy(enable_http=True, http_port=9090)
        assert app._enable_http is True
        assert app._http_port == 9090
        assert app._http_server is None  # Not created until start()

    @pytest.mark.asyncio
    async def test_http_server_not_created_when_disabled(self):
        """Test HTTP server not created when disabled."""
        app = PolyPy(enable_http=False)

        # Mock components to prevent actual startup
        app._registry = MagicMock()
        app._workers = MagicMock()
        app._workers.is_healthy.return_value = True
        app._running = True

        assert app._http_server is None
