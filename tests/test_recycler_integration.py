"""Integration tests for ConnectionRecycler with real components."""

import asyncio

import pytest

from src.connection.pool import ConnectionPool
from src.lifecycle.recycler import POLLUTION_THRESHOLD, ConnectionRecycler
from src.registry.asset_registry import AssetRegistry


@pytest.mark.asyncio
async def test_recycler_integrates_with_pool():
    """Recycler can be created with real pool and registry."""
    registry = AssetRegistry()

    def mock_callback(msg):
        pass

    pool = ConnectionPool(registry, mock_callback)
    recycler = ConnectionRecycler(registry, pool)

    assert recycler.is_running is False
    assert recycler.stats.recycles_initiated == 0


@pytest.mark.asyncio
async def test_full_recycling_flow_with_real_components(monkeypatch):
    """
    End-to-end recycling with real AssetRegistry and mocked pool.

    Simulates:
    1. Connection with high pollution (40%)
    2. Recycler detects trigger
    3. Creates new connection
    4. Registry reassigns markets
    5. Old connection removed
    """
    from unittest.mock import AsyncMock, Mock

    from src.lifecycle import recycler as recycler_module

    # Speed up for testing
    monkeypatch.setattr(recycler_module, "HEALTH_CHECK_INTERVAL", 0.1)
    monkeypatch.setattr(recycler_module, "STABILIZATION_DELAY", 0.1)

    # Create real registry
    registry = AssetRegistry()

    # Add markets to registry
    await registry.add_asset("asset-1", "condition-1", expiration_ts=9999999999000)
    await registry.add_asset("asset-2", "condition-1", expiration_ts=9999999999000)
    await registry.add_asset("asset-3", "condition-1", expiration_ts=1000)  # Expired
    await registry.add_asset("asset-4", "condition-1", expiration_ts=1000)  # Expired
    await registry.add_asset("asset-5", "condition-1", expiration_ts=9999999999000)

    # Mark as subscribed to old connection
    await registry.mark_subscribed(
        ["asset-1", "asset-2", "asset-3", "asset-4", "asset-5"], "conn-old"
    )

    # Mark some as expired (creates 40% pollution)
    await registry.mark_expired(["asset-3", "asset-4"])

    # Verify pollution
    stats = registry.connection_stats("conn-old")
    assert stats["pollution_ratio"] >= POLLUTION_THRESHOLD

    # Mock pool
    mock_pool = Mock()
    mock_pool.get_connection_stats = Mock(
        return_value=[
            {
                "connection_id": "conn-old",
                "pollution_ratio": stats["pollution_ratio"],
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]
    )
    mock_pool.force_subscribe = AsyncMock(return_value="conn-new")
    mock_pool._remove_connection = AsyncMock()

    # Update mock to return healthy new connection
    def get_stats_dynamic():
        return [
            {
                "connection_id": "conn-old",
                "pollution_ratio": stats["pollution_ratio"],
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            },
            {"connection_id": "conn-new", "is_healthy": True},
        ]

    mock_pool.get_connection_stats = Mock(side_effect=get_stats_dynamic)

    # Create recycler
    recycler = ConnectionRecycler(registry, mock_pool)

    # Start recycler
    await recycler.start()

    # Wait for recycling to trigger
    await asyncio.sleep(0.3)

    # Stop recycler
    await recycler.stop()

    # Verify recycling occurred
    assert recycler.stats.recycles_initiated >= 1
    assert recycler.stats.recycles_completed >= 1
    assert recycler.stats.markets_migrated == 3  # Only active markets

    # Verify registry state
    active_on_new = registry.get_active_by_connection("conn-new")
    assert len(active_on_new) == 3
    assert "asset-1" in active_on_new
    assert "asset-2" in active_on_new
    assert "asset-5" in active_on_new

    # Verify old connection removed from pool
    mock_pool._remove_connection.assert_called_with("conn-old")


@pytest.mark.asyncio
async def test_concurrent_recycles_limited(monkeypatch):
    """Concurrent recycles limited to MAX_CONCURRENT_RECYCLES."""
    from unittest.mock import AsyncMock, Mock

    from src.lifecycle import recycler as recycler_module

    monkeypatch.setattr(recycler_module, "STABILIZATION_DELAY", 0.2)

    registry = AssetRegistry()

    # Add assets for multiple connections
    for i in range(5):
        await registry.add_asset(f"asset-{i}", "condition-1", expiration_ts=1000)
        await registry.mark_subscribed([f"asset-{i}"], f"conn-{i}")
        await registry.mark_expired([f"asset-{i}"])

    # Mock pool
    mock_pool = Mock()

    connections = []
    for i in range(5):
        connections.append(
            {
                "connection_id": f"conn-{i}",
                "pollution_ratio": 1.0,  # 100% expired
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        )

    mock_pool.get_connection_stats = Mock(return_value=connections)
    mock_pool._remove_connection = AsyncMock()

    # Create recycler
    recycler = ConnectionRecycler(registry, mock_pool)

    # Trigger all recycles manually
    tasks = [asyncio.create_task(recycler.force_recycle(f"conn-{i}")) for i in range(5)]

    # Wait a bit and check active
    await asyncio.sleep(0.05)

    # Should have at most MAX_CONCURRENT_RECYCLES active
    active = recycler.get_active_recycles()
    assert len(active) <= recycler_module.MAX_CONCURRENT_RECYCLES

    # Wait for all to complete
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_age_based_recycling(monkeypatch):
    """Recycling triggers on connection age >= 24 hours."""
    from unittest.mock import AsyncMock, Mock

    from src.lifecycle import recycler as recycler_module

    monkeypatch.setattr(recycler_module, "HEALTH_CHECK_INTERVAL", 0.1)
    monkeypatch.setattr(recycler_module, "STABILIZATION_DELAY", 0.05)

    registry = AssetRegistry()
    await registry.add_asset("asset-1", "condition-1", expiration_ts=9999999999000)
    await registry.mark_subscribed(["asset-1"], "conn-old")

    mock_pool = Mock()
    mock_pool.get_connection_stats = Mock(
        return_value=[
            {
                "connection_id": "conn-old",
                "pollution_ratio": 0.0,  # Not polluted
                "age_seconds": 86500.0,  # Over 24 hours
                "is_healthy": True,
                "is_draining": False,
            }
        ]
    )
    mock_pool.force_subscribe = AsyncMock(return_value="conn-new")
    mock_pool._remove_connection = AsyncMock()

    # Update mock for new connection
    def get_stats_dynamic():
        return [
            {
                "connection_id": "conn-old",
                "pollution_ratio": 0.0,
                "age_seconds": 86500.0,
                "is_healthy": True,
                "is_draining": False,
            },
            {"connection_id": "conn-new", "is_healthy": True},
        ]

    mock_pool.get_connection_stats = Mock(side_effect=get_stats_dynamic)

    recycler = ConnectionRecycler(registry, mock_pool)
    await recycler.start()
    await asyncio.sleep(0.3)
    await recycler.stop()

    # Should have triggered recycling due to age
    assert recycler.stats.recycles_initiated >= 1


@pytest.mark.asyncio
async def test_unhealthy_connection_recycling(monkeypatch):
    """Recycling triggers on unhealthy connection."""
    from unittest.mock import AsyncMock, Mock

    from src.lifecycle import recycler as recycler_module

    monkeypatch.setattr(recycler_module, "HEALTH_CHECK_INTERVAL", 0.1)
    monkeypatch.setattr(recycler_module, "STABILIZATION_DELAY", 0.05)

    registry = AssetRegistry()
    await registry.add_asset("asset-1", "condition-1", expiration_ts=9999999999000)
    await registry.mark_subscribed(["asset-1"], "conn-old")

    mock_pool = Mock()
    mock_pool.get_connection_stats = Mock(
        return_value=[
            {
                "connection_id": "conn-old",
                "pollution_ratio": 0.0,
                "age_seconds": 100.0,
                "is_healthy": False,  # Unhealthy
                "is_draining": False,
            }
        ]
    )
    mock_pool.force_subscribe = AsyncMock(return_value="conn-new")
    mock_pool._remove_connection = AsyncMock()

    def get_stats_dynamic():
        return [
            {
                "connection_id": "conn-old",
                "pollution_ratio": 0.0,
                "age_seconds": 100.0,
                "is_healthy": False,
                "is_draining": False,
            },
            {"connection_id": "conn-new", "is_healthy": True},
        ]

    mock_pool.get_connection_stats = Mock(side_effect=get_stats_dynamic)

    recycler = ConnectionRecycler(registry, mock_pool)
    await recycler.start()
    await asyncio.sleep(0.3)
    await recycler.stop()

    assert recycler.stats.recycles_initiated >= 1
