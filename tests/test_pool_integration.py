"""Integration tests for ConnectionPool with real registry and multiple connections."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connection.pool import ConnectionPool
from src.connection.stats import ConnectionStats
from src.connection.types import ConnectionStatus
from src.messages.parser import MessageParser
from src.registry.asset_registry import AssetRegistry


@pytest.mark.asyncio
async def test_pool_with_registry_integration():
    """Test pool with real registry managing 1000+ markets."""
    registry = AssetRegistry()
    parser = MessageParser()

    # Mock callback
    async def message_callback(connection_id, message):
        pass

    pool = ConnectionPool(registry, message_callback, parser)

    # Add 1000 pending markets
    for i in range(1000):
        await registry.add_asset(
            asset_id=f"asset-{i}",
            condition_id=f"market-{i // 2}",  # 2 assets per market
            expiration_ts=0,
        )

    # Mock WebsocketConnection to avoid real connections
    with patch("src.connection.pool.WebsocketConnection") as mock_ws_class:

        def create_mock_connection(*args, **kwargs):
            mock_conn = MagicMock()
            mock_conn.status = ConnectionStatus.CONNECTED
            mock_conn.start = AsyncMock()
            mock_conn.stop = AsyncMock()
            mock_conn.mark_draining = MagicMock()
            mock_stats = ConnectionStats()
            mock_conn.stats = mock_stats
            mock_conn.is_healthy = True
            return mock_conn

        mock_ws_class.side_effect = create_mock_connection

        # Start pool
        await pool.start()

        # Shorten interval for testing
        import src.connection.pool

        original_interval = src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL
        src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = 0.1

        try:
            # Wait for subscription loop to process
            await asyncio.sleep(1.5)  # Multiple intervals to process all batches

            # Verify connections created (1000 / 400 = 3 connections)
            assert pool.connection_count >= 2, (
                f"Expected >= 2 connections, got {pool.connection_count}"
            )
            assert registry.get_pending_count() < 1000

            # Verify capacity tracking
            stats = pool.get_connection_stats()
            total_subscribed = sum(s["subscribed_markets"] for s in stats)
            assert total_subscribed > 800, (
                f"Expected > 800 subscribed, got {total_subscribed}"
            )

        finally:
            src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = original_interval
            # Cleanup
            await pool.stop()


@pytest.mark.asyncio
async def test_capacity_tracking():
    """Verify capacity tracking with multiple connections."""
    registry = AssetRegistry()
    parser = MessageParser()

    async def message_callback(connection_id, message):
        pass

    pool = ConnectionPool(registry, message_callback, parser)

    # Add exactly 800 pending markets
    for i in range(800):
        await registry.add_asset(f"asset-{i}", f"market-{i}", 0)

    # Mock WebsocketConnection
    with patch("src.connection.pool.WebsocketConnection") as mock_ws_class:

        def create_mock_connection(*args, **kwargs):
            mock_conn = MagicMock()
            mock_conn.status = ConnectionStatus.CONNECTED
            mock_conn.start = AsyncMock()
            mock_conn.stop = AsyncMock()
            mock_stats = ConnectionStats()
            mock_conn.stats = mock_stats
            mock_conn.is_healthy = True
            return mock_conn

        mock_ws_class.side_effect = create_mock_connection

        await pool.start()

        # Shorten interval for testing
        import src.connection.pool

        original_interval = src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL
        src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = 0.1

        try:
            await asyncio.sleep(1.0)

            # Should create 2 connections (400 each)
            assert pool.connection_count == 2, (
                f"Expected 2 connections, got {pool.connection_count}"
            )

            # Check total capacity
            capacity = pool.get_total_capacity()
            assert capacity == 0, (
                f"Expected 0 capacity (all slots used), got {capacity}"
            )

        finally:
            src.connection.pool.BATCH_SUBSCRIPTION_INTERVAL = original_interval
            await pool.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown():
    """Verify all connections stop within timeout."""
    registry = AssetRegistry()
    parser = MessageParser()

    async def message_callback(connection_id, message):
        pass

    pool = ConnectionPool(registry, message_callback, parser)

    # Mock WebsocketConnection
    with patch("src.connection.pool.WebsocketConnection") as mock_ws_class:
        mock_connections = []

        def create_mock_connection(*args, **kwargs):
            mock_conn = MagicMock()
            mock_conn.status = ConnectionStatus.CONNECTED
            mock_conn.start = AsyncMock()
            mock_conn.stop = AsyncMock()
            mock_stats = ConnectionStats()
            mock_conn.stats = mock_stats
            mock_conn.is_healthy = True
            mock_connections.append(mock_conn)
            return mock_conn

        mock_ws_class.side_effect = create_mock_connection

        # Start pool first
        await pool.start()

        # Create 5 connections via force_subscribe
        for i in range(5):
            # Add asset to registry first
            await registry.add_asset(f"asset-{i}", f"market-{i}", 0)
            await pool.force_subscribe([f"asset-{i}"])

        assert pool.connection_count == 5

        # Stop and measure time
        start = time.monotonic()
        await pool.stop()
        elapsed = time.monotonic() - start

        # Verify all connections were stopped
        assert pool.connection_count == 0, (
            f"Expected 0 connections after stop, got {pool.connection_count}"
        )
        assert elapsed < 10.0, f"Shutdown took {elapsed:.2f}s, expected < 10s"

        # Verify stop() was called on all mock connections
        for mock_conn in mock_connections:
            mock_conn.stop.assert_called_once()
