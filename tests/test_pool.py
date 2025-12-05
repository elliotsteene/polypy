"""Tests for ConnectionPool."""

import asyncio
import time

import pytest

from src.connection.pool import ConnectionInfo, ConnectionPool
from src.connection.types import ConnectionStatus
from src.messages.parser import MessageParser
from src.registry.asset_registry import AssetRegistry


class TestConnectionInfo:
    """Tests for ConnectionInfo dataclass."""

    def test_connection_info_age_tracking(self):
        """Test that ConnectionInfo correctly tracks age."""
        # Arrange
        from unittest.mock import MagicMock

        mock_connection = MagicMock()

        # Act
        info = ConnectionInfo(connection=mock_connection)
        time.sleep(0.1)  # Wait a bit
        age = info.age

        # Assert
        assert age >= 0.1, "Age should be at least 0.1 seconds"
        assert age < 0.2, "Age should be less than 0.2 seconds"

    def test_connection_info_default_not_draining(self):
        """Test that ConnectionInfo defaults to not draining."""
        # Arrange
        from unittest.mock import MagicMock

        mock_connection = MagicMock()

        # Act
        info = ConnectionInfo(connection=mock_connection)

        # Assert
        assert info.is_draining is False


class TestConnectionPoolInitialization:
    """Tests for ConnectionPool initialization."""

    @pytest.mark.asyncio
    async def test_pool_initialization(self):
        """Test ConnectionPool initializes with correct defaults."""
        # Arrange
        registry = AssetRegistry()
        parser = MessageParser()

        async def callback(connection_id, message):
            pass

        # Act
        pool = ConnectionPool(registry, callback, parser)

        # Assert
        assert pool.connection_count == 0
        assert pool.active_connection_count == 0
        assert pool.get_total_capacity() == 0
        assert pool._registry is registry
        assert pool._message_callback is callback
        assert pool._message_parser is parser
        assert pool._running is False

    @pytest.mark.asyncio
    async def test_pool_creates_parser_if_not_provided(self):
        """Test ConnectionPool creates MessageParser if not provided."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        # Act
        pool = ConnectionPool(registry, callback)

        # Assert
        assert pool._message_parser is not None
        assert isinstance(pool._message_parser, MessageParser)


class TestConnectionPoolLifecycle:
    """Tests for ConnectionPool start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self):
        """Test that start() sets the running flag."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        await pool.start()

        # Assert
        assert pool._running is True
        assert pool._subscription_task is not None

        # Cleanup
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self):
        """Test that stop() clears the running flag."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)
        await pool.start()

        # Act
        await pool.stop()

        # Assert
        assert pool._running is False
        assert len(pool._connections) == 0

    @pytest.mark.asyncio
    async def test_multiple_start_calls_are_idempotent(self):
        """Test that calling start() multiple times is safe."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        await pool.start()
        first_task = pool._subscription_task
        await pool.start()  # Second call
        second_task = pool._subscription_task

        # Assert
        assert first_task is second_task
        assert pool._running is True

        # Cleanup
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        """Test that calling stop() without start() is safe."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act & Assert (should not raise)
        await pool.stop()
        assert pool._running is False

    @pytest.mark.asyncio
    async def test_start_creates_subscription_task(self):
        """Test that start() creates the subscription task."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        await pool.start()
        await asyncio.sleep(0.1)  # Let task start

        # Assert
        assert pool._subscription_task is not None
        assert not pool._subscription_task.done()

        # Cleanup
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_subscription_task(self):
        """Test that stop() cancels the subscription task."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)
        await pool.start()
        subscription_task = pool._subscription_task

        # Act
        await pool.stop()
        await asyncio.sleep(0.1)  # Let task finish cancellation

        # Assert
        assert subscription_task.cancelled() or subscription_task.done()


class TestConnectionPoolProperties:
    """Tests for ConnectionPool properties."""

    @pytest.mark.asyncio
    async def test_connection_count_with_no_connections(self):
        """Test connection_count returns 0 when no connections exist."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        count = pool.connection_count

        # Assert
        assert count == 0

    @pytest.mark.asyncio
    async def test_active_connection_count_with_no_connections(self):
        """Test active_connection_count returns 0 when no connections exist."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        count = pool.active_connection_count

        # Assert
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_total_capacity_with_no_connections(self):
        """Test get_total_capacity returns 0 when no connections exist."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        capacity = pool.get_total_capacity()

        # Assert
        assert capacity == 0

    @pytest.mark.asyncio
    async def test_connection_count_with_mock_connections(self):
        """Test connection_count includes all connections."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add mock connections directly
        mock_conn1 = MagicMock()
        mock_conn1.status = ConnectionStatus.CONNECTED
        mock_conn2 = MagicMock()
        mock_conn2.status = ConnectionStatus.CONNECTED

        pool._connections["conn1"] = ConnectionInfo(connection=mock_conn1)
        pool._connections["conn2"] = ConnectionInfo(connection=mock_conn2)

        # Act
        count = pool.connection_count

        # Assert
        assert count == 2

    @pytest.mark.asyncio
    async def test_active_connection_count_excludes_draining(self):
        """Test active_connection_count excludes draining connections."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add mock connections
        mock_conn1 = MagicMock()
        mock_conn1.status = ConnectionStatus.CONNECTED
        mock_conn2 = MagicMock()
        mock_conn2.status = ConnectionStatus.CONNECTED

        info1 = ConnectionInfo(connection=mock_conn1, is_draining=False)
        info2 = ConnectionInfo(connection=mock_conn2, is_draining=True)

        pool._connections["conn1"] = info1
        pool._connections["conn2"] = info2

        # Act
        count = pool.active_connection_count

        # Assert
        assert count == 1  # Only conn1 should be counted

    @pytest.mark.asyncio
    async def test_get_total_capacity_calculates_correctly(self):
        """Test get_total_capacity calculates available slots."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add assets to registry for conn1 (100 assets)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed([f"asset-{i}" for i in range(100)], "conn1")

        # Add mock connection
        mock_conn1 = MagicMock()
        mock_conn1.status = ConnectionStatus.CONNECTED
        pool._connections["conn1"] = ConnectionInfo(connection=mock_conn1)

        # Act
        capacity = pool.get_total_capacity()

        # Assert
        # TARGET_MARKETS_PER_CONNECTION = 400
        # Used = 100, so available = 400 - 100 = 300
        assert capacity == 300

    @pytest.mark.asyncio
    async def test_get_total_capacity_excludes_draining_connections(self):
        """Test get_total_capacity excludes draining connections."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add mock connections
        mock_conn1 = MagicMock()
        mock_conn1.status = ConnectionStatus.CONNECTED
        mock_conn2 = MagicMock()
        mock_conn2.status = ConnectionStatus.CONNECTED

        info1 = ConnectionInfo(connection=mock_conn1, is_draining=False)
        info2 = ConnectionInfo(connection=mock_conn2, is_draining=True)

        pool._connections["conn1"] = info1
        pool._connections["conn2"] = info2

        # Act
        capacity = pool.get_total_capacity()

        # Assert
        # Only conn1 should be counted (400 available)
        # conn2 is draining so excluded
        assert capacity == 400


class TestSubscriptionLoop:
    """Tests for subscription loop and pending market processing."""

    @pytest.mark.asyncio
    async def test_process_pending_markets_below_threshold(self):
        """Test that no connection is created when pending count is below threshold."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add 40 pending markets (below MIN_PENDING_FOR_NEW_CONNECTION = 50)
        for i in range(40):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        # Act
        await pool._process_pending_markets()

        # Assert
        assert pool.connection_count == 0
        assert registry.get_pending_count() == 40  # Nothing consumed

    @pytest.mark.asyncio
    async def test_process_pending_markets_creates_connection(self):
        """Test that connection is created when pending count exceeds threshold."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add 100 pending markets (above threshold)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        # Act
        await pool._process_pending_markets()

        # Assert
        assert pool.connection_count == 1
        assert registry.get_pending_count() == 0  # All consumed (100 < 400)

        # Verify connection is in pool
        connection_ids = list(pool._connections.keys())
        assert len(connection_ids) == 1
        assert connection_ids[0].startswith("conn-")

    @pytest.mark.asyncio
    async def test_process_pending_markets_batches_correctly(self):
        """Test that pending markets are batched at TARGET_MARKETS_PER_CONNECTION."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add 449 pending markets (more than TARGET_MARKETS_PER_CONNECTION = 400)
        for i in range(449):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        # Act - First batch
        await pool._process_pending_markets()

        # Assert - First batch
        assert pool.connection_count == 1
        assert registry.get_pending_count() == 49  # 449 - 400 = 49 remaining

        # Act - Second batch (below threshold of 50, no new connection)
        await pool._process_pending_markets()

        # Assert - Second batch
        assert pool.connection_count == 1  # No new connection (49 < MIN_PENDING=50)
        assert registry.get_pending_count() == 49  # Still 49 pending

    @pytest.mark.asyncio
    async def test_process_pending_markets_marks_subscribed(self):
        """Test that processed markets are marked as subscribed in registry."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add 100 pending markets
        asset_ids = [f"asset-{i}" for i in range(100)]
        for asset_id in asset_ids:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        # Act
        await pool._process_pending_markets()

        # Assert
        connection_id = list(pool._connections.keys())[0]
        subscribed_assets = registry.get_by_connection(connection_id)
        assert len(subscribed_assets) == 100
        assert all(asset_id in subscribed_assets for asset_id in asset_ids)

        # Verify status changed from PENDING to SUBSCRIBED
        from src.registry.asset_entry import AssetStatus

        for asset_id in asset_ids:
            entry = registry.get(asset_id)
            assert entry is not None
            assert entry.status == AssetStatus.SUBSCRIBED
            assert entry.connection_id == connection_id

    @pytest.mark.asyncio
    async def test_subscription_loop_processes_pending_markets(self):
        """Test that subscription loop automatically processes pending markets."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add 100 pending markets
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        # Mock the sleep to speed up test
        import src.connection.pool

        original_interval = src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL
        src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = 0.1  # 100ms for testing

        try:
            # Act
            await pool.start()
            await asyncio.sleep(0.3)  # Wait for loop to process

            # Assert
            assert pool.connection_count >= 1
            assert registry.get_pending_count() == 0

        finally:
            # Cleanup
            src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = original_interval
            await pool.stop()

    @pytest.mark.asyncio
    async def test_subscription_loop_handles_multiple_batches(self):
        """Test that subscription loop handles multiple batches over time."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add initial batch
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        # Mock the sleep
        import src.connection.pool

        original_interval = src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL
        src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = 0.1

        try:
            await pool.start()
            await asyncio.sleep(0.3)  # First batch processed

            # Add more pending markets
            for i in range(100, 200):
                await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

            await asyncio.sleep(0.3)  # Second batch processed

            # Assert
            assert pool.connection_count >= 2
            assert registry.get_pending_count() == 0

        finally:
            src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = original_interval
            await pool.stop()

    @pytest.mark.asyncio
    async def test_subscription_loop_stops_cleanly(self):
        """Test that subscription loop stops cleanly without errors."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        import src.connection.pool

        original_interval = src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL
        src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = 0.1

        try:
            # Act
            await pool.start()
            await asyncio.sleep(0.3)
            await pool.stop()

            # Assert - task should be cancelled/done
            assert pool._subscription_task.cancelled() or pool._subscription_task.done()
            assert pool._running is False

        finally:
            src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = original_interval

    @pytest.mark.asyncio
    async def test_process_pending_markets_with_empty_queue(self):
        """Test that processing works correctly when queue becomes empty."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # No pending markets

        # Act - Should not raise error
        await pool._process_pending_markets()

        # Assert
        assert pool.connection_count == 0
        assert registry.get_pending_count() == 0


class TestRecyclingDetection:
    """Tests for connection recycling detection and workflow."""

    @pytest.mark.asyncio
    async def test_check_for_recycling_triggers_at_pollution_threshold(self):
        """Test that recycling is triggered when pollution >= 30%."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create a connection with assets
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        info = ConnectionInfo(connection=mock_conn)
        # Make it old enough (> 5 min)
        info.created_at = time.monotonic() - 400.0

        pool._connections[connection_id] = info

        # Add 100 assets, 30 expired (30% pollution)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(30)])

        # Act
        await pool._check_for_recycling()

        # Give time for recycling task to start
        await asyncio.sleep(0.1)

        # Assert - connection should be marked as draining
        assert info.is_draining is True

    @pytest.mark.asyncio
    async def test_check_for_recycling_skips_young_connections(self):
        """Test that recycling skips connections younger than 5 minutes."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create a young connection (< 5 min)
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        info = ConnectionInfo(connection=mock_conn)
        # Make it young (2 min old)
        info.created_at = time.monotonic() - 120.0

        pool._connections[connection_id] = info

        # Add 100 assets, 80 expired (80% pollution - way over threshold)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(80)])

        # Act
        await pool._check_for_recycling()
        await asyncio.sleep(0.1)

        # Assert - connection should NOT be draining (too young)
        assert info.is_draining is False

    @pytest.mark.asyncio
    async def test_check_for_recycling_skips_draining_connections(self):
        """Test that recycling skips connections already draining."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create a connection already draining
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        info = ConnectionInfo(connection=mock_conn, is_draining=True)
        info.created_at = time.monotonic() - 400.0

        pool._connections[connection_id] = info

        # Add 100 assets, 80 expired
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(80)])

        # Act
        await pool._check_for_recycling()
        await asyncio.sleep(0.1)

        # Assert - connection should still be draining (not changed)
        # No new task should have been created for recycling
        assert info.is_draining is True
        assert pool.connection_count == 1  # Still just the one connection

    @pytest.mark.asyncio
    async def test_check_for_recycling_skips_below_pollution_threshold(self):
        """Test that recycling skips connections below pollution threshold."""
        # Arrange
        from unittest.mock import MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create an old connection
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        info = ConnectionInfo(connection=mock_conn)
        info.created_at = time.monotonic() - 400.0

        pool._connections[connection_id] = info

        # Add 100 assets, only 20 expired (20% pollution - below 30% threshold)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(20)])

        # Act
        await pool._check_for_recycling()
        await asyncio.sleep(0.1)

        # Assert - connection should NOT be draining
        assert info.is_draining is False

    @pytest.mark.asyncio
    async def test_initiate_recycling_with_no_active_assets(self):
        """Test that recycling closes connection when no active assets remain."""
        # Arrange
        from unittest.mock import AsyncMock, MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create a connection
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        mock_conn.stop = AsyncMock()
        mock_conn.mark_draining = MagicMock()

        info = ConnectionInfo(connection=mock_conn)
        pool._connections[connection_id] = info

        # Add 100 assets, all expired (no active assets)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(100)])

        # Act
        await pool._initiate_recycling(connection_id)

        # Assert - connection should be removed
        assert connection_id not in pool._connections
        mock_conn.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_initiate_recycling_creates_replacement_connection(self):
        """Test that recycling creates replacement connection with active assets."""
        # Arrange
        from unittest.mock import AsyncMock, MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create old connection
        old_connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        mock_conn.stop = AsyncMock()
        mock_conn.mark_draining = MagicMock()

        info = ConnectionInfo(connection=mock_conn)
        pool._connections[old_connection_id] = info

        # Add 100 assets, 30 expired, 70 active
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], old_connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(30)])

        initial_count = pool.connection_count

        # Act
        await pool._initiate_recycling(old_connection_id)

        # Assert - new connection should be created
        # Note: initial connection is removed, new one added, so count stays same
        assert pool.connection_count == initial_count

        # Old connection should be removed
        assert old_connection_id not in pool._connections

        # New connection should exist
        new_connection_ids = [
            cid for cid in pool._connections.keys() if cid != old_connection_id
        ]
        assert len(new_connection_ids) == 1

        new_connection_id = new_connection_ids[0]

        # 70 active assets should be reassigned to new connection
        new_assets = registry.get_by_connection(new_connection_id)
        assert len(new_assets) == 70

    @pytest.mark.asyncio
    async def test_initiate_recycling_with_nonexistent_connection(self):
        """Test that recycling handles nonexistent connection gracefully."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act - try to recycle nonexistent connection
        await pool._initiate_recycling("nonexistent-conn")

        # Assert - should not raise error, just log warning
        assert pool.connection_count == 0

    @pytest.mark.asyncio
    async def test_remove_connection_removes_from_pool_and_registry(self):
        """Test that remove_connection cleans up properly."""
        # Arrange
        from unittest.mock import AsyncMock, MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create connection and add assets
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.stop = AsyncMock()

        pool._connections[connection_id] = ConnectionInfo(connection=mock_conn)

        for i in range(50):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed([f"asset-{i}" for i in range(50)], connection_id)

        # Act
        await pool._remove_connection(connection_id)

        # Assert
        assert connection_id not in pool._connections
        mock_conn.stop.assert_called_once()

        # Assets should be orphaned (connection_id = None)
        assets = registry.get_by_connection(connection_id)
        assert len(assets) == 0

    @pytest.mark.asyncio
    async def test_recycling_integration_with_subscription_loop(self):
        """Test that stub recycling can be manually triggered via _check_for_recycling."""
        # NOTE: Automatic recycling is now handled by ConnectionRecycler
        # This test verifies the stub implementation can still be manually triggered
        from unittest.mock import AsyncMock, MagicMock

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Create old connection with high pollution
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        mock_conn.mark_draining = MagicMock()
        mock_conn.stop = AsyncMock()

        info = ConnectionInfo(connection=mock_conn)
        info.created_at = time.monotonic() - 400.0  # 6+ min old
        pool._connections[connection_id] = info

        # Add assets with high pollution
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(70)])  # 70% pollution

        # Act - manually trigger check
        await pool._check_for_recycling()
        await asyncio.sleep(0.2)  # Give time for recycling task to run

        # Assert - recycling should be triggered, connection marked as draining
        assert info.is_draining is True


class TestStatsAndForceSubscribe:
    """Tests for stats aggregation and force subscribe functionality."""

    @pytest.mark.asyncio
    async def test_get_connection_stats_with_no_connections(self):
        """Test get_connection_stats returns empty list when no connections exist."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        stats = pool.get_connection_stats()

        # Assert
        assert isinstance(stats, list)
        assert len(stats) == 0

    @pytest.mark.asyncio
    async def test_get_connection_stats_returns_correct_structure(self):
        """Test get_connection_stats returns correctly structured data."""
        # Arrange
        from unittest.mock import MagicMock

        from src.connection.stats import ConnectionStats

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add a mock connection
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        mock_stats = ConnectionStats()
        mock_stats.messages_received = 100
        mock_stats.bytes_received = 5000
        mock_stats.parse_errors = 2
        mock_stats.reconnect_count = 1
        mock_conn.stats = mock_stats
        mock_conn.is_healthy = True

        pool._connections[connection_id] = ConnectionInfo(connection=mock_conn)

        # Add some assets to registry
        for i in range(50):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed([f"asset-{i}" for i in range(50)], connection_id)

        # Act
        stats = pool.get_connection_stats()

        # Assert
        assert len(stats) == 1
        stat = stats[0]

        # Check all expected keys exist
        expected_keys = {
            "connection_id",
            "status",
            "is_draining",
            "age_seconds",
            "messages_received",
            "bytes_received",
            "parse_errors",
            "reconnect_count",
            "is_healthy",
            "total_markets",
            "subscribed_markets",
            "expired_markets",
            "pollution_ratio",
        }
        assert set(stat.keys()) == expected_keys

        # Check values
        assert stat["connection_id"] == connection_id
        assert stat["status"] == "CONNECTED"
        assert stat["is_draining"] is False
        assert stat["age_seconds"] >= 0
        assert stat["messages_received"] == 100
        assert stat["bytes_received"] == 5000
        assert stat["parse_errors"] == 2
        assert stat["reconnect_count"] == 1
        assert stat["is_healthy"] is True
        assert stat["total_markets"] == 50
        assert stat["subscribed_markets"] == 50
        assert stat["expired_markets"] == 0
        assert stat["pollution_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_get_connection_stats_with_multiple_connections(self):
        """Test get_connection_stats aggregates data from multiple connections."""
        # Arrange
        from unittest.mock import MagicMock

        from src.connection.stats import ConnectionStats

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add two mock connections
        for idx in range(2):
            connection_id = f"test-conn-{idx}"
            mock_conn = MagicMock()
            mock_conn.status = ConnectionStatus.CONNECTED
            mock_stats = ConnectionStats()
            mock_stats.messages_received = 100 * (idx + 1)
            mock_conn.stats = mock_stats
            mock_conn.is_healthy = True

            pool._connections[connection_id] = ConnectionInfo(connection=mock_conn)

            # Add assets
            asset_count = 50 * (idx + 1)
            for i in range(asset_count):
                await registry.add_asset(f"asset-{idx}-{i}", f"market-{idx}-{i}", 0)

            await registry.mark_subscribed(
                [f"asset-{idx}-{i}" for i in range(asset_count)], connection_id
            )

        # Act
        stats = pool.get_connection_stats()

        # Assert
        assert len(stats) == 2

        # Verify each has correct market counts
        conn0_stats = next(s for s in stats if s["connection_id"] == "test-conn-0")
        conn1_stats = next(s for s in stats if s["connection_id"] == "test-conn-1")

        assert conn0_stats["total_markets"] == 50
        assert conn0_stats["messages_received"] == 100

        assert conn1_stats["total_markets"] == 100
        assert conn1_stats["messages_received"] == 200

    @pytest.mark.asyncio
    async def test_get_connection_stats_calculates_pollution_ratio(self):
        """Test get_connection_stats includes correct pollution ratio."""
        # Arrange
        from unittest.mock import MagicMock

        from src.connection.stats import ConnectionStats

        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add a connection with some expired assets
        connection_id = "test-conn-1"
        mock_conn = MagicMock()
        mock_conn.status = ConnectionStatus.CONNECTED
        mock_conn.stats = ConnectionStats()
        mock_conn.is_healthy = True

        pool._connections[connection_id] = ConnectionInfo(connection=mock_conn)

        # Add 100 assets, expire 30 (30% pollution)
        for i in range(100):
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

        await registry.mark_subscribed(
            [f"asset-{i}" for i in range(100)], connection_id
        )
        await registry.mark_expired([f"asset-{i}" for i in range(30)])

        # Act
        stats = pool.get_connection_stats()

        # Assert
        assert len(stats) == 1
        stat = stats[0]

        assert stat["total_markets"] == 100
        assert stat["subscribed_markets"] == 70  # 100 - 30 expired
        assert stat["expired_markets"] == 30
        assert stat["pollution_ratio"] == 0.30

    @pytest.mark.asyncio
    async def test_force_subscribe_creates_immediate_connection(self):
        """Test force_subscribe creates connection immediately."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add assets to registry first (force_subscribe expects them to exist)
        asset_ids = [f"priority-asset-{i}" for i in range(100)]
        for asset_id in asset_ids:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        # Act
        connection_id = await pool.force_subscribe(asset_ids)

        # Assert
        assert connection_id.startswith("conn-")
        assert pool.connection_count == 1
        assert connection_id in pool._connections

        # Verify assets are subscribed
        subscribed = registry.get_by_connection(connection_id)
        assert len(subscribed) == 100
        assert all(asset_id in subscribed for asset_id in asset_ids)

    @pytest.mark.asyncio
    async def test_force_subscribe_returns_connection_id(self):
        """Test force_subscribe returns the connection_id string."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act
        asset_ids = ["asset-1", "asset-2", "asset-3"]
        connection_id = await pool.force_subscribe(asset_ids)

        # Assert
        assert isinstance(connection_id, str)
        assert len(connection_id) > 0

    @pytest.mark.asyncio
    async def test_force_subscribe_raises_error_for_too_many_assets(self):
        """Test force_subscribe raises ValueError for >500 assets."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act & Assert
        asset_ids = [f"asset-{i}" for i in range(501)]

        with pytest.raises(ValueError) as exc_info:
            await pool.force_subscribe(asset_ids)

        assert "Cannot subscribe to more than 500 markets" in str(exc_info.value)
        assert "got 501" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_force_subscribe_raises_error_for_empty_list(self):
        """Test force_subscribe raises ValueError for empty asset list."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await pool.force_subscribe([])

        assert "Cannot force subscribe to empty asset list" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_force_subscribe_bypasses_pending_queue(self):
        """Test force_subscribe bypasses the pending queue."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add some pending assets (below threshold, so won't be processed automatically)
        for i in range(30):
            await registry.add_asset(f"pending-asset-{i}", f"market-{i}", 0)

        # Add priority assets to registry
        priority_assets = [f"priority-asset-{i}" for i in range(50)]
        for asset_id in priority_assets:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        # Act - Force subscribe priority assets
        connection_id = await pool.force_subscribe(priority_assets)

        # Assert
        assert pool.connection_count == 1

        # Pending assets should still be in queue (30 original pending + 50 priority = 80 total pending before force_subscribe)
        # After force_subscribe, the 50 priority assets are no longer pending
        assert registry.get_pending_count() == 30

        # Only priority assets should be subscribed
        subscribed = registry.get_by_connection(connection_id)
        assert len(subscribed) == 50
        assert all(asset_id in subscribed for asset_id in priority_assets)

    @pytest.mark.asyncio
    async def test_force_subscribe_with_max_assets(self):
        """Test force_subscribe works with exactly 500 assets (max allowed)."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add assets to registry first
        asset_ids = [f"asset-{i}" for i in range(500)]
        for asset_id in asset_ids:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        # Act - Subscribe exactly 500 assets
        connection_id = await pool.force_subscribe(asset_ids)

        # Assert
        assert pool.connection_count == 1
        subscribed = registry.get_by_connection(connection_id)
        assert len(subscribed) == 500

    @pytest.mark.asyncio
    async def test_force_subscribe_multiple_times(self):
        """Test force_subscribe can be called multiple times."""
        # Arrange
        registry = AssetRegistry()

        async def callback(connection_id, message):
            pass

        pool = ConnectionPool(registry, callback)

        # Add assets to registry first
        assets1 = [f"batch1-asset-{i}" for i in range(50)]
        assets2 = [f"batch2-asset-{i}" for i in range(50)]

        for asset_id in assets1:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        for asset_id in assets2:
            await registry.add_asset(asset_id, f"market-{asset_id}", 0)

        # Act - Force subscribe twice
        conn1 = await pool.force_subscribe(assets1)
        conn2 = await pool.force_subscribe(assets2)

        # Assert
        assert pool.connection_count == 2
        assert conn1 != conn2

        # Each connection should have its own assets
        subscribed1 = registry.get_by_connection(conn1)
        subscribed2 = registry.get_by_connection(conn2)

        assert len(subscribed1) == 50
        assert len(subscribed2) == 50
        assert subscribed1.isdisjoint(subscribed2)  # No overlap
