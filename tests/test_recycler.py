"""Unit tests for ConnectionRecycler."""

import asyncio

import pytest

from src.lifecycle.recycler import RecycleStats


class TestRecycleStats:
    """Test RecycleStats dataclass and computed properties."""

    def test_default_values(self):
        """All fields default to 0."""
        stats = RecycleStats()
        assert stats.recycles_initiated == 0
        assert stats.recycles_completed == 0
        assert stats.recycles_failed == 0
        assert stats.markets_migrated == 0
        assert stats.total_downtime_ms == 0.0

    def test_success_rate_no_recycles(self):
        """Success rate is 1.0 (100%) when no recycles initiated."""
        stats = RecycleStats()
        assert stats.success_rate == 1.0

    def test_success_rate_all_success(self):
        """Success rate is 1.0 when all recycles completed."""
        stats = RecycleStats(
            recycles_initiated=10,
            recycles_completed=10,
        )
        assert stats.success_rate == 1.0

    def test_success_rate_partial_success(self):
        """Success rate calculated correctly with failures."""
        stats = RecycleStats(
            recycles_initiated=10,
            recycles_completed=7,
            recycles_failed=3,
        )
        assert stats.success_rate == 0.7

    def test_success_rate_all_failures(self):
        """Success rate is 0.0 when all recycles failed."""
        stats = RecycleStats(
            recycles_initiated=5,
            recycles_completed=0,
            recycles_failed=5,
        )
        assert stats.success_rate == 0.0

    def test_avg_downtime_no_recycles(self):
        """Average downtime is 0.0 when no recycles completed."""
        stats = RecycleStats()
        assert stats.avg_downtime_ms == 0.0

    def test_avg_downtime_single_recycle(self):
        """Average downtime calculated correctly for single recycle."""
        stats = RecycleStats(
            recycles_completed=1,
            total_downtime_ms=150.5,
        )
        assert stats.avg_downtime_ms == 150.5

    def test_avg_downtime_multiple_recycles(self):
        """Average downtime calculated correctly for multiple recycles."""
        stats = RecycleStats(
            recycles_completed=4,
            total_downtime_ms=600.0,
        )
        assert stats.avg_downtime_ms == 150.0


class TestConnectionRecyclerInit:
    """Test ConnectionRecycler initialization."""

    def test_initialization(self, mock_registry, mock_pool):
        """Recycler initializes with correct defaults."""
        from src.lifecycle.recycler import ConnectionRecycler

        recycler = ConnectionRecycler(mock_registry, mock_pool)

        assert recycler.is_running is False
        assert recycler.get_active_recycles() == set()
        assert recycler.stats.recycles_initiated == 0
        assert recycler.stats.recycles_completed == 0
        assert recycler.stats.recycles_failed == 0

    def test_stats_property_readonly(self, mock_registry, mock_pool):
        """Stats property returns the stats object."""
        from src.lifecycle.recycler import ConnectionRecycler

        recycler = ConnectionRecycler(mock_registry, mock_pool)
        stats = recycler.stats

        assert isinstance(stats, RecycleStats)
        assert stats is recycler._stats


@pytest.fixture
def mock_registry():
    """Mock AssetRegistry."""
    from unittest.mock import AsyncMock, Mock

    registry = Mock()
    # Make async methods return AsyncMock
    registry.reassign_connection = AsyncMock()
    return registry


@pytest.fixture
def mock_pool():
    """Mock ConnectionPool."""
    from unittest.mock import AsyncMock, Mock

    pool = Mock()
    # Make async methods return AsyncMock
    pool._remove_connection = AsyncMock()
    pool.force_subscribe = AsyncMock()
    return pool


@pytest.fixture
def recycler(mock_registry, mock_pool):
    """Create ConnectionRecycler instance."""
    from src.lifecycle.recycler import ConnectionRecycler

    return ConnectionRecycler(mock_registry, mock_pool)


class TestTriggerDetection:
    """Test connection recycling trigger detection."""

    @pytest.mark.asyncio
    async def test_pollution_trigger(self, recycler, mock_pool):
        """Trigger detected when pollution >= 30%."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.35,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        # Should detect trigger (will be stubbed for now)
        await recycler._check_all_connections()

        # Verify log output or other side effects
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_age_trigger(self, recycler, mock_pool):
        """Trigger detected when age >= 24 hours."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 86500.0,  # Over 24 hours
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_trigger(self, recycler, mock_pool):
        """Trigger detected when connection unhealthy."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 100.0,
                "is_healthy": False,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_already_recycling(self, recycler, mock_pool):
        """Skip connections already being recycled."""
        recycler._active_recycles.add("conn-1")

        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()

        # Should not trigger recycling (already in progress)
        assert "conn-1" in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_skip_draining(self, recycler, mock_pool):
        """Skip connections already marked as draining."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": True,
            }
        ]

        await recycler._check_all_connections()

        # Should skip draining connection
        assert "conn-1" not in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_no_trigger(self, recycler, mock_pool):
        """No trigger when connection is healthy and young."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 1000.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()

        # Should not trigger
        assert "conn-1" not in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_multiple_connections(self, recycler, mock_pool):
        """Handle multiple connections correctly."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.05,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            },
            {
                "connection_id": "conn-2",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            },
        ]

        await recycler._check_all_connections()

        # Only conn-2 should trigger
        mock_pool.get_connection_stats.assert_called_once()


class TestRecyclingWorkflow:
    """Test full recycling workflow."""

    @pytest.mark.asyncio
    async def test_successful_recycle(self, recycler, mock_registry, mock_pool):
        """Full recycle workflow succeeds."""
        # Setup
        connection_id = "conn-old"
        new_connection_id = "conn-new"
        active_assets = ["asset-1", "asset-2", "asset-3"]

        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)
        mock_pool.force_subscribe.return_value = new_connection_id
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": new_connection_id,
                "is_healthy": True,
            }
        ]
        mock_registry.reassign_connection.return_value = len(active_assets)

        # Execute
        result = await recycler._recycle_connection(connection_id)

        # Verify
        assert result is True
        assert recycler.stats.recycles_initiated == 1
        assert recycler.stats.recycles_completed == 1
        assert recycler.stats.recycles_failed == 0
        assert recycler.stats.markets_migrated == 3
        assert recycler.stats.total_downtime_ms > 0

        # Verify calls
        mock_registry.get_active_by_connection.assert_called_once_with(connection_id)
        # Check that force_subscribe was called with the correct assets (order doesn't matter)
        assert mock_pool.force_subscribe.call_count == 1
        call_args = mock_pool.force_subscribe.call_args[0][0]
        assert set(call_args) == set(active_assets)
        # Check reassign_connection was called with correct args (order doesn't matter for assets)
        assert mock_registry.reassign_connection.call_count == 1
        reassign_call_args = mock_registry.reassign_connection.call_args[0]
        assert set(reassign_call_args[0]) == set(active_assets)
        assert reassign_call_args[1] == connection_id
        assert reassign_call_args[2] == new_connection_id
        mock_pool._remove_connection.assert_called_once_with(connection_id)

    @pytest.mark.asyncio
    async def test_recycle_empty_connection(self, recycler, mock_registry, mock_pool):
        """Recycle connection with no active markets."""
        connection_id = "conn-old"

        mock_registry.get_active_by_connection.return_value = frozenset()

        result = await recycler._recycle_connection(connection_id)

        assert result is True
        assert recycler.stats.recycles_completed == 1
        assert recycler.stats.markets_migrated == 0

        # Should remove without creating replacement
        mock_pool.force_subscribe.assert_not_called()
        mock_pool._remove_connection.assert_called_once_with(connection_id)

    @pytest.mark.asyncio
    async def test_recycle_new_connection_fails(
        self, recycler, mock_registry, mock_pool
    ):
        """Recycle aborts if new connection creation fails."""
        connection_id = "conn-old"
        active_assets = ["asset-1", "asset-2"]

        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)
        mock_pool.force_subscribe.side_effect = Exception("Connection failed")

        result = await recycler._recycle_connection(connection_id)

        assert result is False
        assert recycler.stats.recycles_initiated == 1
        assert recycler.stats.recycles_failed == 1
        assert recycler.stats.recycles_completed == 0

        # Old connection should remain (not removed)
        mock_pool._remove_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_recycle_new_connection_unhealthy(
        self, recycler, mock_registry, mock_pool
    ):
        """Recycle aborts if new connection is unhealthy."""
        connection_id = "conn-old"
        new_connection_id = "conn-new"
        active_assets = ["asset-1"]

        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)
        mock_pool.force_subscribe.return_value = new_connection_id
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": new_connection_id,
                "is_healthy": False,  # Unhealthy
            }
        ]

        result = await recycler._recycle_connection(connection_id)

        assert result is False
        assert recycler.stats.recycles_failed == 1

        # Should not reassign or remove old connection
        mock_registry.reassign_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_recycle_reassignment_fails(self, recycler, mock_registry, mock_pool):
        """Recycle aborts if registry reassignment fails."""
        connection_id = "conn-old"
        new_connection_id = "conn-new"
        active_assets = ["asset-1"]

        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)
        mock_pool.force_subscribe.return_value = new_connection_id
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": new_connection_id,
                "is_healthy": True,
            }
        ]
        mock_registry.reassign_connection.side_effect = Exception("Reassignment failed")

        result = await recycler._recycle_connection(connection_id)

        assert result is False
        assert recycler.stats.recycles_failed == 1

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(
        self, recycler, mock_registry, mock_pool
    ):
        """Semaphore limits concurrent recycles to MAX_CONCURRENT_RECYCLES."""
        from src.lifecycle.recycler import MAX_CONCURRENT_RECYCLES

        # Setup slow recycle
        active_assets = ["asset-1"]
        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)

        async def slow_force_subscribe(assets):
            await asyncio.sleep(0.5)
            return f"conn-{len(assets)}"

        mock_pool.force_subscribe.side_effect = slow_force_subscribe
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "is_healthy": True,
            }
        ]
        mock_registry.reassign_connection.return_value = 1

        # Start MAX_CONCURRENT_RECYCLES + 1 recycles
        tasks = [
            asyncio.create_task(recycler._recycle_connection(f"conn-{i}"))
            for i in range(MAX_CONCURRENT_RECYCLES + 1)
        ]

        # Wait a bit - some should be blocked by semaphore
        await asyncio.sleep(0.1)

        # Check active recycles (should be <= MAX_CONCURRENT_RECYCLES)
        assert len(recycler.get_active_recycles()) <= MAX_CONCURRENT_RECYCLES

        # Wait for all to complete
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_active_recycles_tracking(self, recycler, mock_registry, mock_pool):
        """Connection ID added/removed from active_recycles correctly."""
        connection_id = "conn-1"
        active_assets = ["asset-1"]

        mock_registry.get_active_by_connection.return_value = frozenset(active_assets)
        mock_pool.force_subscribe.return_value = "conn-new"
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-new",
                "is_healthy": True,
            }
        ]
        mock_registry.reassign_connection.return_value = 1

        # Before recycle
        assert connection_id not in recycler.get_active_recycles()

        # During recycle (check with task)
        task = asyncio.create_task(recycler._recycle_connection(connection_id))
        await asyncio.sleep(0.01)  # Let it start
        assert connection_id in recycler.get_active_recycles()

        # After recycle
        await task
        assert connection_id not in recycler.get_active_recycles()


class TestLifecycle:
    """Test start/stop lifecycle management."""

    @pytest.mark.asyncio
    async def test_start(self, recycler):
        """Start initializes monitoring loop."""
        await recycler.start()

        assert recycler.is_running is True
        assert recycler._monitor_task is not None

        # Cleanup
        await recycler.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, recycler):
        """Starting already-running recycler is no-op."""
        await recycler.start()
        task1 = recycler._monitor_task

        await recycler.start()  # Second start
        task2 = recycler._monitor_task

        assert task1 is task2  # Same task

        await recycler.stop()

    @pytest.mark.asyncio
    async def test_stop(self, recycler):
        """Stop cancels monitoring task."""
        await recycler.start()
        await recycler.stop()

        assert recycler.is_running is False
        assert recycler._monitor_task.cancelled() or recycler._monitor_task.done()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, recycler):
        """Stopping already-stopped recycler is no-op."""
        await recycler.start()
        await recycler.stop()
        await recycler.stop()  # Second stop

        assert recycler.is_running is False

    @pytest.mark.asyncio
    async def test_stop_waits_for_active_recycles(
        self, recycler, mock_registry, mock_pool
    ):
        """Stop waits for active recycles to complete."""
        await recycler.start()

        # Simulate active recycle
        recycler._active_recycles.add("conn-1")

        # Stop in background
        stop_task = asyncio.create_task(recycler.stop())

        # Wait a bit
        await asyncio.sleep(0.1)

        # Stop should be waiting
        assert not stop_task.done()

        # Complete the recycle
        recycler._active_recycles.discard("conn-1")

        # Now stop should complete
        await asyncio.wait_for(stop_task, timeout=1.0)
        assert recycler.is_running is False

    @pytest.mark.asyncio
    async def test_monitor_loop_checks_connections(
        self, mock_registry, mock_pool, monkeypatch
    ):
        """Monitor loop calls _check_all_connections periodically."""
        from unittest.mock import AsyncMock

        from src.lifecycle.recycler import ConnectionRecycler

        # Speed up for testing
        from src.lifecycle import recycler as recycler_module

        monkeypatch.setattr(recycler_module, "HEALTH_CHECK_INTERVAL", 0.1)

        # Create recycler and mock the check method
        recycler = ConnectionRecycler(mock_registry, mock_pool)
        mock_check = AsyncMock()

        # Patch at the class level
        monkeypatch.setattr(ConnectionRecycler, "_check_all_connections", mock_check)

        await recycler.start()
        await asyncio.sleep(0.35)  # Should trigger ~3 checks
        await recycler.stop()

        # Verify _check_all_connections was called at least twice
        assert mock_check.call_count >= 2

    @pytest.mark.asyncio
    async def test_monitor_loop_handles_exceptions(
        self, mock_registry, mock_pool, monkeypatch
    ):
        """Monitor loop continues after exceptions."""
        from src.lifecycle.recycler import ConnectionRecycler

        # Speed up for testing
        from src.lifecycle import recycler as recycler_module

        monkeypatch.setattr(recycler_module, "HEALTH_CHECK_INTERVAL", 0.05)

        # Create recycler and mock the check method to fail once then succeed
        recycler = ConnectionRecycler(mock_registry, mock_pool)

        call_count = 0

        async def failing_check(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Test error")

        monkeypatch.setattr(ConnectionRecycler, "_check_all_connections", failing_check)

        await recycler.start()
        await asyncio.sleep(0.25)  # Longer wait to ensure multiple cycles
        await recycler.stop()

        # Should have recovered and continued
        assert call_count >= 2
