"""Integration tests for WorkerManager with MessageRouter."""

import asyncio

import pytest

from src.messages.parser import MessageParser
from src.router import MessageRouter
from src.worker import WorkerManager


@pytest.fixture
def worker_manager():
    """Create WorkerManager for testing."""
    manager = WorkerManager(num_workers=2)
    yield manager
    if manager._running:
        manager.stop()


@pytest.fixture
def message_router(worker_manager):
    """Create MessageRouter with WorkerManager queues."""
    queues = worker_manager.get_input_queues()
    router = MessageRouter(num_workers=2, worker_queues=queues)
    yield router
    if router._running:
        asyncio.run(router.stop())


@pytest.mark.asyncio
async def test_integration_router_to_worker(worker_manager, message_router):
    """Test message flow from router to worker to orderbook state."""
    # Start components
    worker_manager.start()
    await message_router.start()

    # Create test message (book snapshot)
    parser = MessageParser()
    raw_message = b'{"event_type":"book","asset_id":"123","market":"0xabc","timestamp":1234567890,"hash":"test","bids":[{"price":"0.50","size":"100"}],"asks":[{"price":"0.51","size":"100"}]}'

    messages = list(parser.parse_messages(raw_message))
    assert len(messages) == 1

    # Route message
    success = await message_router.route_message("conn1", messages[0])
    assert success

    # Wait for processing
    await asyncio.sleep(0.5)

    # Cleanup - workers send final stats on shutdown
    await message_router.stop()
    worker_manager.stop()

    # Verify stats show processing (sent during shutdown)
    stats = worker_manager.get_stats()
    assert len(stats) > 0

    # At least one worker processed messages
    total_processed = sum(s.messages_processed for s in stats.values())
    assert total_processed >= 1


@pytest.mark.asyncio
async def test_integration_multiple_workers(worker_manager, message_router):
    """Test messages distributed across multiple workers."""
    worker_manager.start()
    await message_router.start()

    parser = MessageParser()

    # Send messages for different assets
    for asset_id in ["asset1", "asset2", "asset3", "asset4"]:
        raw = (
            f'{{"event_type":"book","asset_id":"{asset_id}",'
            f'"market":"0xabc","timestamp":1234567890,"hash":"test",'
            f'"bids":[{{"price":"0.50","size":"100"}}],"asks":[{{"price":"0.51","size":"100"}}]}}'
        ).encode()

        messages = list(parser.parse_messages(raw))
        await message_router.route_message("conn1", messages[0])

    # Wait for processing
    await asyncio.sleep(1.0)

    # Cleanup - workers send final stats on shutdown
    await message_router.stop()
    worker_manager.stop()

    # Both workers should have processed messages (stats sent during shutdown)
    stats = worker_manager.get_stats()
    assert len(stats) == 2

    worker0_count = stats[0].messages_processed if 0 in stats else 0
    worker1_count = stats[1].messages_processed if 1 in stats else 0

    assert worker0_count > 0
    assert worker1_count > 0


def test_integration_worker_crash_detection(worker_manager):
    """Test health monitoring detects crashed worker."""
    worker_manager.start()

    # Initially healthy
    assert worker_manager.is_healthy()
    assert worker_manager.get_alive_count() == 2

    # Kill a worker
    worker_manager._processes[0].terminate()
    worker_manager._processes[0].join(timeout=2.0)

    # Should detect unhealthy
    assert not worker_manager.is_healthy()
    assert worker_manager.get_alive_count() == 1

    # Cleanup
    worker_manager.stop()
