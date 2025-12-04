"""Tests for MessageRouter."""

import asyncio
import time
from multiprocessing import Queue as MPQueue

import pytest

from src.messages.protocol import EventType, ParsedMessage, PriceChange, Side
from src.router import (
    ASYNC_QUEUE_SIZE,
    WORKER_QUEUE_SIZE,
    MessageRouter,
    RouterStats,
    _hash_to_worker,
)


def _create_test_router(num_workers: int) -> MessageRouter:
    """Helper to create MessageRouter with test queues."""
    queues = [MPQueue(maxsize=WORKER_QUEUE_SIZE) for _ in range(num_workers)]
    return MessageRouter(num_workers=num_workers, worker_queues=queues)


class TestRouterStats:
    """Tests for RouterStats dataclass."""

    def test_default_values(self):
        stats = RouterStats()
        assert stats.messages_routed == 0
        assert stats.messages_dropped == 0
        assert stats.batches_sent == 0
        assert stats.queue_full_events == 0
        assert stats.routing_errors == 0
        assert stats.total_latency_ms == 0.0

    def test_avg_latency_no_messages(self):
        stats = RouterStats()
        assert stats.avg_latency_ms == 0.0

    def test_avg_latency_with_messages(self):
        stats = RouterStats(messages_routed=10, total_latency_ms=50.0)
        assert stats.avg_latency_ms == 5.0


class TestHashFunction:
    """Tests for consistent hash function."""

    def test_deterministic(self):
        """Same asset_id always hashes to same worker."""
        asset_id = "test-asset-123"
        worker1 = _hash_to_worker(asset_id, 4)
        worker2 = _hash_to_worker(asset_id, 4)
        assert worker1 == worker2

    def test_range(self):
        """Hash result is within valid range."""
        for i in range(100):
            worker = _hash_to_worker(f"asset-{i}", 4)
            assert 0 <= worker < 4

    def test_distribution(self):
        """Hash distributes reasonably across workers."""
        counts = {i: 0 for i in range(4)}
        for i in range(1000):
            worker = _hash_to_worker(f"asset-{i}", 4)
            counts[worker] += 1

        # Each worker should get at least some messages (>10%)
        for count in counts.values():
            assert count > 100


class TestMessageRouterInit:
    """Tests for MessageRouter initialization."""

    def test_init_creates_queues(self):
        router = _create_test_router(num_workers=4)
        assert len(router.get_worker_queues()) == 4
        assert router.num_workers == 4

    def test_init_zero_workers_raises(self):
        queues = []
        with pytest.raises(ValueError):
            MessageRouter(num_workers=0, worker_queues=queues)

    def test_init_negative_workers_raises(self):
        queues = []
        with pytest.raises(ValueError):
            MessageRouter(num_workers=-1, worker_queues=queues)

    def test_init_mismatched_queues_raises(self):
        queues = [MPQueue(), MPQueue()]
        with pytest.raises(ValueError):
            MessageRouter(num_workers=3, worker_queues=queues)

    def test_stats_initial(self):
        router = _create_test_router(num_workers=2)
        assert router.stats.messages_routed == 0

    def test_queue_depths_initial(self):
        router = _create_test_router(num_workers=2)
        depths = router.get_queue_depths()
        assert depths["async_queue"] == 0
        # Note: worker queue depths may be -1 on macOS (qsize not implemented)
        assert "worker_0" in depths
        assert "worker_1" in depths


class TestWorkerAssignment:
    """Tests for asset-to-worker assignment."""

    def test_get_worker_for_asset_cached(self):
        router = _create_test_router(num_workers=4)
        asset_id = "test-asset"

        worker1 = router.get_worker_for_asset(asset_id)
        worker2 = router.get_worker_for_asset(asset_id)

        assert worker1 == worker2
        assert asset_id in router._asset_worker_cache


def _make_price_change_message(asset_id: str) -> ParsedMessage:
    """Helper to create a test message."""
    return ParsedMessage(
        event_type=EventType.PRICE_CHANGE,
        asset_id=asset_id,
        market="test-market",
        raw_timestamp=int(time.time() * 1000),
        price_change=PriceChange(
            asset_id=asset_id,
            price=500,
            size=100,
            side=Side.BUY,
            hash="test-hash",
            best_bid=500,
            best_ask=510,
        ),
    )


class TestRouteMessage:
    """Tests for route_message method."""

    @pytest.mark.asyncio
    async def test_route_message_success(self):
        router = _create_test_router(num_workers=2)
        message = _make_price_change_message("asset-1")

        result = await router.route_message("conn-1", message)

        assert result is True
        # Check async queue has the message
        depths = router.get_queue_depths()
        assert depths["async_queue"] == 1

    @pytest.mark.asyncio
    async def test_route_message_backpressure(self):
        """Test that messages are dropped when queue is full."""
        # Create router with tiny queue for testing
        router = _create_test_router(num_workers=1)
        # Fill the async queue
        for i in range(ASYNC_QUEUE_SIZE):
            message = _make_price_change_message(f"asset-{i}")
            await router.route_message("conn-1", message)

        # Next message should be dropped
        message = _make_price_change_message("asset-overflow")
        result = await router.route_message("conn-1", message)

        assert result is False
        assert router.stats.messages_dropped == 1


class TestRoutingLoop:
    """Integration tests for routing loop."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        router = _create_test_router(num_workers=2)
        await router.start()
        assert router._running is True
        assert router._routing_task is not None

        await router.stop()
        assert router._running is False

    @pytest.mark.asyncio
    async def test_messages_routed_to_workers(self):
        router = _create_test_router(num_workers=2)
        await router.start()

        # Send messages
        for i in range(10):
            message = _make_price_change_message(f"asset-{i}")
            await router.route_message("conn-1", message)

        # Give routing loop time to process
        await asyncio.sleep(0.05)

        await router.stop()

        # Check stats
        assert router.stats.messages_routed == 10
        assert router.stats.batches_sent >= 1

    @pytest.mark.asyncio
    async def test_consistent_routing(self):
        """Same asset always goes to same worker."""
        router = _create_test_router(num_workers=4)
        await router.start()

        asset_id = "test-asset"
        expected_worker = router.get_worker_for_asset(asset_id)

        # Send multiple messages for same asset
        for _ in range(5):
            message = _make_price_change_message(asset_id)
            await router.route_message("conn-1", message)

        await asyncio.sleep(0.05)
        await router.stop()

        # Verify messages went to expected worker by attempting to retrieve them
        queues = router.get_worker_queues()
        expected_queue = queues[expected_worker]

        # Should be able to get 5 messages + None sentinel from expected worker
        messages_received = 0
        while True:
            try:
                item = expected_queue.get_nowait()
                if item is None:
                    break
                messages_received += 1
            except Exception:
                break

        assert messages_received == 5

    @pytest.mark.asyncio
    async def test_stop_sends_sentinels(self):
        router = _create_test_router(num_workers=2)
        await router.start()

        # Send a few messages first
        for i in range(3):
            message = _make_price_change_message(f"asset-{i}")
            await router.route_message("conn-1", message)

        # Give time to process
        await asyncio.sleep(0.05)

        await router.stop()

        # Each worker queue should have messages and/or None sentinel
        # Count how many None sentinels we can find
        sentinel_count = 0
        for q in router.get_worker_queues():
            # Drain the queue looking for sentinel
            while True:
                try:
                    item = q.get(timeout=0.1)
                    if item is None:
                        sentinel_count += 1
                        break
                except Exception:
                    break

        # We should have sent one sentinel per worker
        assert sentinel_count == 2


class TestRouterEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_start_when_already_running(self):
        """Test starting router when already running."""
        router = _create_test_router(num_workers=2)
        await router.start()

        # Try to start again
        await router.start()  # Should log warning and return

        await router.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        """Test stopping router when not running."""
        router = _create_test_router(num_workers=2)

        # Stop without starting - should return early
        await router.stop()

        # Router should still be in not-running state
        assert not router._running

    @pytest.mark.asyncio
    async def test_queue_full_during_shutdown(self):
        """Test handling Full exception when sending shutdown sentinels."""
        from queue import Full
        from unittest.mock import Mock

        router = _create_test_router(num_workers=2)
        await router.start()

        # Mock worker queues to raise Full on put_nowait
        for queue in router._worker_queues:
            queue.put_nowait = Mock(side_effect=Full())

        # Stop should handle the Full exception gracefully
        await router.stop()

    @pytest.mark.asyncio
    async def test_worker_queue_full_message_dropped(self):
        """Test messages dropped when worker queue is full."""
        # Create router with small worker queues
        small_queues = [MPQueue(maxsize=1) for _ in range(2)]
        router = MessageRouter(num_workers=2, worker_queues=small_queues)
        await router.start()

        # Send many messages to fill up queues
        for i in range(20):
            message = _make_price_change_message(f"asset-{i}")
            await router.route_message("conn-1", message)

        # Give routing loop time to try processing
        await asyncio.sleep(0.2)

        await router.stop()

        # Some messages should have been dropped due to full queues
        assert router.stats.messages_dropped > 0
        assert router.stats.queue_full_events > 0

    @pytest.mark.asyncio
    async def test_batch_timeout_behavior(self):
        """Test that batching respects timeout even if batch not full."""
        from src.router import BATCH_TIMEOUT

        router = _create_test_router(num_workers=2)
        await router.start()

        # Send just one message (won't fill batch)
        message = _make_price_change_message("asset-1")
        await router.route_message("conn-1", message)

        # Wait longer than batch timeout
        await asyncio.sleep(BATCH_TIMEOUT + 0.05)

        await router.stop()

        # Message should have been routed despite incomplete batch
        assert router.stats.messages_routed == 1
        assert router.stats.batches_sent >= 1
