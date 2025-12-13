"""Integration tests for PolyPy Application orchestrator.

These tests verify the application works correctly as an integrated system.
They are marked with @pytest.mark.serial to run sequentially, preventing
resource exhaustion from parallel execution.
"""

import asyncio
import time

import pytest

from src.app import PolyPy


@pytest.fixture(scope="function")
async def app():
    """
    Fixture that provides a PolyPy instance and ensures proper cleanup.

    This fixture is critical for preventing resource leaks in integration tests.
    The app is created with 2 workers to minimize resource usage.
    """
    _app = PolyPy(num_workers=2)
    yield _app

    # Ensure cleanup even if test fails
    if _app.is_running:
        await _app.stop()

    # Wait for all resources to be fully released
    # This is critical - worker processes need time to fully terminate
    await asyncio.sleep(1.0)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_full_startup_shutdown_cycle(app: PolyPy):
    """Test complete startup and shutdown sequence."""
    # Start application
    await app.start()

    # Verify running
    assert app.is_running
    assert app.is_healthy()

    # Wait for stabilization
    await asyncio.sleep(1.0)

    # Check stats
    stats = app.get_stats()
    assert stats["running"]
    assert stats["workers"]["alive_count"] == 2
    assert stats["workers"]["is_healthy"]

    # Verify components initialized
    assert stats["registry"]["total_markets"] >= 0
    assert "connection_count" in stats["pool"]
    assert "messages_routed" in stats["router"]
    assert "recycles_initiated" in stats["recycler"]

    # Stop application
    await app.stop()
    await asyncio.sleep(0.5)

    # Verify stopped
    assert not app.is_running
    assert not app.is_healthy()


@pytest.mark.asyncio
@pytest.mark.serial
async def test_signal_handling_via_event(app: PolyPy):
    """Test shutdown via event (simulates signal handling)."""
    # Create run task
    run_task = asyncio.create_task(app.run())

    # Wait for startup
    await asyncio.sleep(2.0)

    assert app.is_running
    assert app.is_healthy()

    # Trigger shutdown via event (simulates signal)
    app._shutdown_event.set()

    # Wait for shutdown
    await run_task

    assert not app.is_running


@pytest.mark.asyncio
@pytest.mark.serial
async def test_market_discovery(app: PolyPy):
    """Test that lifecycle controller discovers markets."""
    await app.start()

    # Wait for initial discovery
    await asyncio.sleep(3.0)

    # Check lifecycle stats
    stats = app.get_stats()
    lifecycle_stats = stats.get("lifecycle", {})

    # Verify lifecycle structure exists
    assert "known_market_count" in lifecycle_stats
    assert "is_running" in lifecycle_stats
    assert lifecycle_stats["is_running"] is True

    # If markets discovered, verify registry reflects them
    if lifecycle_stats.get("known_market_count", 0) > 0:
        assert stats["registry"]["total_markets"] >= 0

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_worker_processes_alive(app: PolyPy):
    """Test that worker processes are created and alive."""
    await app.start()
    await asyncio.sleep(1.0)

    # Check worker stats
    stats = app.get_stats()
    assert stats["workers"]["alive_count"] == 2
    assert stats["workers"]["is_healthy"] is True

    # Verify worker stats structure
    worker_stats = stats["workers"]["worker_stats"]
    assert isinstance(worker_stats, dict)

    # If worker stats available, verify fields
    if worker_stats:
        for worker_id, ws in worker_stats.items():
            assert "messages_processed" in ws
            assert "updates_applied" in ws
            assert "snapshots_received" in ws
            assert "orderbook_count" in ws
            assert "memory_usage_mb" in ws

    await app.stop()
    await asyncio.sleep(0.5)

    # Verify workers stopped
    stats = app.get_stats()
    assert stats["workers"]["alive_count"] == 0
    assert stats["workers"]["is_healthy"] is False


@pytest.mark.asyncio
@pytest.mark.serial
async def test_pool_subscription_system(app: PolyPy):
    """Test that connection pool and subscription system work."""
    await app.start()
    await asyncio.sleep(2.0)

    # Check pool stats
    stats = app.get_stats()
    pool_stats = stats["pool"]

    assert "connection_count" in pool_stats
    assert "active_connections" in pool_stats
    assert "total_capacity" in pool_stats
    assert "stats" in pool_stats

    # Connection count might be 0 if no markets discovered yet
    assert pool_stats["connection_count"] >= 0

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_router_initialization(app: PolyPy):
    """Test that message router is initialized and running."""
    await app.start()
    await asyncio.sleep(1.0)

    # Check router stats
    stats = app.get_stats()
    router_stats = stats["router"]

    assert "messages_routed" in router_stats
    assert "messages_dropped" in router_stats
    assert "batches_sent" in router_stats
    assert "queue_full_events" in router_stats
    assert "routing_errors" in router_stats
    assert "avg_latency_ms" in router_stats
    assert "queue_depths" in router_stats

    # Initial values should be 0
    assert router_stats["messages_routed"] >= 0
    assert router_stats["messages_dropped"] >= 0

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_recycler_initialization(app: PolyPy):
    """Test that connection recycler is initialized."""
    await app.start()
    await asyncio.sleep(1.0)

    # Check recycler stats
    stats = app.get_stats()
    recycler_stats = stats["recycler"]

    assert "recycles_initiated" in recycler_stats
    assert "recycles_completed" in recycler_stats
    assert "recycles_failed" in recycler_stats
    assert "success_rate" in recycler_stats
    assert "markets_migrated" in recycler_stats
    assert "avg_downtime_ms" in recycler_stats
    assert "active_recycles" in recycler_stats

    # Initial values should be 0/empty
    assert recycler_stats["recycles_initiated"] == 0
    assert recycler_stats["active_recycles"] == []

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_multiple_start_stop_cycles(app: PolyPy):
    """Test that application can be started and stopped multiple times."""
    # First cycle
    await app.start()
    await asyncio.sleep(0.5)
    assert app.is_running
    await app.stop()
    await asyncio.sleep(1.0)  # Extra time for full cleanup
    assert not app.is_running

    # Second cycle
    await app.start()
    await asyncio.sleep(0.5)
    assert app.is_running
    await app.stop()
    await asyncio.sleep(1.0)  # Extra time for full cleanup
    assert not app.is_running


@pytest.mark.asyncio
@pytest.mark.serial
async def test_shutdown_completes_quickly(app: PolyPy):
    """Test that shutdown completes within reasonable time."""
    await app.start()
    await asyncio.sleep(1.0)

    # Measure shutdown time
    start_time = time.monotonic()
    await app.stop()
    shutdown_duration = time.monotonic() - start_time

    # Should complete within 15 seconds (well under the 30s target)
    assert shutdown_duration < 15.0


@pytest.mark.asyncio
@pytest.mark.serial
async def test_health_check_reflects_state(app: PolyPy):
    """Test that health check accurately reflects application state."""
    # Not running yet
    assert not app.is_healthy()

    # Start application
    await app.start()
    await asyncio.sleep(1.0)

    # Should be healthy
    assert app.is_healthy()

    # Stop application
    await app.stop()
    await asyncio.sleep(0.5)

    # Should not be healthy
    assert not app.is_healthy()


@pytest.mark.asyncio
@pytest.mark.serial
async def test_stats_accuracy_during_operation(app: PolyPy):
    """Test that stats remain accurate during operation."""
    await app.start()

    # Get initial stats
    stats1 = app.get_stats()
    assert stats1["running"] is True

    # Wait a bit
    await asyncio.sleep(1.0)

    # Get stats again
    stats2 = app.get_stats()
    assert stats2["running"] is True

    # Worker count should be stable
    assert stats1["workers"]["alive_count"] == stats2["workers"]["alive_count"]
    assert stats1["workers"]["alive_count"] == 2

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_component_integration(app: PolyPy):
    """Test that all components are properly integrated."""
    await app.start()
    await asyncio.sleep(1.5)

    stats = app.get_stats()

    # Verify all major components are operational
    assert stats["running"] is True

    # Registry should be initialized
    assert "total_markets" in stats["registry"]

    # Workers should be running
    assert stats["workers"]["alive_count"] == 2
    assert stats["workers"]["is_healthy"] is True

    # Router should be initialized
    assert stats["router"]["messages_routed"] >= 0

    # Pool should be initialized
    assert stats["pool"]["connection_count"] >= 0

    # Recycler should be initialized
    assert stats["recycler"]["recycles_initiated"] >= 0

    # Overall health should be good
    assert app.is_healthy()

    await app.stop()
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
@pytest.mark.serial
async def test_graceful_shutdown_after_error_during_startup(app: PolyPy):
    """Test that app can be stopped even if start wasn't called."""
    # Test stop() is safe when start() wasn't called
    await app.stop()
    assert not app.is_running
