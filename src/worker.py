"""
Worker Process Manager - Manages CPU-intensive orderbook workers.

Each worker:
- Runs in separate OS process (bypasses GIL)
- Owns subset of orderbooks (determined by consistent hash in router)
- Processes messages from dedicated multiprocessing queue
- Reports health and stats periodically
"""

import logging
import os
import signal
import time
from dataclasses import dataclass
from multiprocessing import Event as MPEvent
from multiprocessing import Process
from multiprocessing import Queue as MPQueue
from multiprocessing.synchronize import Event
from queue import Empty, Full

from src.messages.protocol import EventType, ParsedMessage
from src.orderbook.orderbook_store import Asset, OrderbookStore

logger = logging.getLogger(__name__)

# Configuration constants
QUEUE_TIMEOUT: float = 0.1  # 100ms timeout for queue.get()
HEARTBEAT_INTERVAL: float = 5.0  # Seconds between heartbeat updates
STATS_INTERVAL: float = 30.0  # Seconds between stats reports


@dataclass(slots=True)
class WorkerStats:
    """Per-worker performance statistics."""

    messages_processed: int = 0
    updates_applied: int = 0
    snapshots_received: int = 0
    processing_time_ms: float = 0.0
    last_message_ts: float = 0.0
    orderbook_count: int = 0
    memory_usage_bytes: int = 0

    @property
    def avg_processing_time_us(self) -> float:
        """Average per-message processing time in microseconds."""
        if self.messages_processed > 0:
            return (self.processing_time_ms * 1000) / self.messages_processed
        return 0.0


def _process_message(
    store: OrderbookStore,
    message: ParsedMessage,
    stats: WorkerStats,
) -> None:
    """
    Process single message and update orderbook state.

    Args:
        store: OrderbookStore instance for this worker
        message: Parsed message to process
        stats: Stats tracker to update
    """
    stats.messages_processed += 1

    try:
        match message.event_type:
            case EventType.BOOK:
                _process_book_snapshot(store, message, stats)

            case EventType.PRICE_CHANGE:
                _process_price_change(store, message, stats)

            case EventType.LAST_TRADE_PRICE:
                _process_last_trade(store, message, stats)

            case _:
                logger.warning(f"Unknown event type: {message.event_type}")

    except AssertionError as e:
        logger.error(
            f"Assertion failed processing {message.event_type}: {e}",
            exc_info=True,
        )
    except Exception as e:
        logger.exception(
            f"Error processing {message.event_type} for asset {message.asset_id}: {e}"
        )


def _process_book_snapshot(
    store: OrderbookStore,
    message: ParsedMessage,
    stats: WorkerStats,
) -> None:
    """Process BOOK snapshot message."""
    assert message.book is not None, "BOOK message missing book field"

    asset = Asset(asset_id=message.asset_id, market=message.market)
    orderbook = store.register_asset(asset)
    orderbook.apply_snapshot(message.book, message.raw_timestamp)

    stats.snapshots_received += 1
    stats.updates_applied += 1


def _process_price_change(
    store: OrderbookStore,
    message: ParsedMessage,
    stats: WorkerStats,
) -> None:
    """Process PRICE_CHANGE message."""
    assert message.price_change is not None, (
        "PRICE_CHANGE message missing price_change field"
    )

    orderbook = store.get_state(message.asset_id)
    if orderbook:
        orderbook.apply_price_change(message.price_change, message.raw_timestamp)
        stats.updates_applied += 1
    else:
        logger.debug(f"Skipping price_change for unknown asset {message.asset_id}")


def _process_last_trade(
    store: OrderbookStore,
    message: ParsedMessage,
    stats: WorkerStats,
) -> None:
    """Process LAST_TRADE_PRICE message."""
    assert message.last_trade is not None, (
        "LAST_TRADE_PRICE message missing last_trade field"
    )

    # No state update for trades in v1 (handler_factory deferred)
    # In future, custom handlers would receive callback here
    pass


def _worker_process(
    worker_id: int,
    input_queue: MPQueue,
    stats_queue: MPQueue,
    shutdown_event: Event,
) -> None:
    """
    Worker process entry point with health monitoring.

    Runs in separate process - must be pickleable (module-level function).

    Args:
        worker_id: Unique identifier for this worker (0-indexed)
        input_queue: Queue to receive (ParsedMessage, timestamp) tuples
        stats_queue: Queue to send periodic stats updates
        shutdown_event: Multiprocessing event for shutdown coordination
    """
    # Ignore SIGINT - parent process handles graceful shutdown
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Initialize worker state
    store = OrderbookStore()
    stats = WorkerStats()

    # Initialize timers
    last_heartbeat = time.monotonic()
    last_stats_report = time.monotonic()

    logger.info(f"Worker {worker_id} started")

    try:
        while not shutdown_event.is_set():
            try:
                # Get message with timeout (prevents infinite blocking)
                item = input_queue.get(timeout=QUEUE_TIMEOUT)

                if item is None:
                    # None is shutdown sentinel from router.stop()
                    logger.info(f"Worker {worker_id} received shutdown sentinel")
                    break

                # Unpack message and timestamp
                message, receive_ts = item
                process_start = time.monotonic()

                # Process the message
                _process_message(store, message, stats)

                # Track processing time
                process_time_ms = (time.monotonic() - process_start) * 1000
                stats.processing_time_ms += process_time_ms
                stats.last_message_ts = time.monotonic()

            except Empty:
                # Timeout - check shutdown_event and continue
                pass

            except Exception as e:
                logger.exception(f"Worker {worker_id} error processing message: {e}")
                # Continue processing despite errors

            # Periodic heartbeat - update orderbook and memory stats
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                stats.orderbook_count = len(store._books)
                stats.memory_usage_bytes = store.memory_usage()
                last_heartbeat = now

            # Periodic stats report - send to parent process
            if now - last_stats_report >= STATS_INTERVAL:
                try:
                    # Non-blocking put - drop if queue full (don't block worker)
                    stats_queue.put_nowait((worker_id, stats))
                except Full:
                    # Stats queue full - skip this report
                    pass
                last_stats_report = now

    except Exception as e:
        logger.exception(f"Worker {worker_id} fatal error: {e}")

    finally:
        # Send final stats before exit
        try:
            stats.orderbook_count = len(store._books)
            stats.memory_usage_bytes = store.memory_usage()
            stats_queue.put_nowait((worker_id, stats))
        except Full:
            pass

        logger.info(
            f"Worker {worker_id} stopping. "
            f"Processed {stats.messages_processed} messages, "
            f"managing {stats.orderbook_count} orderbooks, "
            f"memory: {stats.memory_usage_bytes / 1024 / 1024:.2f}MB"
        )


class WorkerManager:
    """
    Manages pool of worker processes.

    Usage:
        manager = WorkerManager(num_workers=4)
        queues = manager.get_input_queues()  # Pass to router
        manager.start()
        # ... run ...
        manager.stop()
    """

    __slots__ = (
        "_num_workers",
        "_processes",
        "_input_queues",
        "_stats_queue",
        "_shutdown_event",
        "_running",
    )

    def __init__(self, num_workers: int) -> None:
        """
        Initialize manager.

        Args:
            num_workers: Number of worker processes to spawn

        Raises:
            ValueError: If num_workers < 1
        """
        if num_workers < 1:
            raise ValueError("num_workers must be at least 1")

        # Warn if num_workers exceeds CPU count
        cpu_count = os.cpu_count() or 1
        if num_workers > cpu_count:
            logger.warning(
                f"num_workers ({num_workers}) exceeds CPU count ({cpu_count}). "
                f"This may cause performance degradation due to context switching."
            )

        self._num_workers = num_workers
        self._processes: list[Process] = []
        self._input_queues: list[MPQueue] = []
        self._stats_queue: MPQueue = MPQueue()
        self._shutdown_event: Event = MPEvent()
        self._running = False

    def get_input_queues(self) -> list[MPQueue]:
        """
        Get input queues for workers.

        Creates queues on first call. Router should use these queues
        and validate count matches num_workers.

        Returns:
            List of multiprocessing.Queue, one per worker
        """
        if not self._input_queues:
            # Match router's WORKER_QUEUE_SIZE (5000)
            self._input_queues = [
                MPQueue(maxsize=5000) for _ in range(self._num_workers)
            ]
        return self._input_queues

    @property
    def num_workers(self) -> int:
        """Number of worker processes."""
        return self._num_workers

    def start(self) -> None:
        """Start all worker processes."""
        if self._running:
            logger.warning("WorkerManager already running")
            return

        self._shutdown_event.clear()
        queues = self.get_input_queues()

        for worker_id in range(self._num_workers):
            process = Process(
                target=_worker_process,
                args=(
                    worker_id,
                    queues[worker_id],
                    self._stats_queue,
                    self._shutdown_event,
                ),
                name=f"orderbook-worker-{worker_id}",
                daemon=False,  # Not daemon - we want explicit lifecycle control
            )
            process.start()
            self._processes.append(process)

        self._running = True
        logger.info(f"WorkerManager started {self._num_workers} worker processes")

    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop all workers gracefully with timeout.

        Uses three-stage shutdown:
        1. Set shutdown_event to signal workers to stop
        2. Send None sentinel to each queue (in case worker blocked on queue)
        3. Wait for graceful exit with timeout, then force terminate

        Args:
            timeout: Max seconds to wait for graceful shutdown per worker
        """
        if not self._running:
            logger.warning("WorkerManager not running")
            return

        logger.info(f"Stopping {self._num_workers} worker processes...")

        # Stage 1: Signal shutdown via event
        self._shutdown_event.set()

        # Stage 2: Send sentinel values to unblock any waiting workers
        for i, queue in enumerate(self._input_queues):
            try:
                queue.put_nowait(None)
            except Full:
                logger.warning(f"Could not send shutdown sentinel to worker {i}")

        # Stage 3: Wait for graceful exit, then force terminate
        deadline = time.monotonic() + timeout

        for process in self._processes:
            remaining = max(0.0, deadline - time.monotonic())
            process.join(timeout=remaining)

            if process.is_alive():
                logger.warning(
                    f"Worker {process.name} did not stop gracefully, force terminating"
                )
                process.terminate()
                process.join(timeout=1.0)

                if process.is_alive():
                    logger.error(f"Worker {process.name} did not terminate, killing")
                    process.kill()
                    process.join(timeout=1.0)

        self._processes.clear()
        self._running = False

        logger.info("All worker processes stopped")

    def get_stats(self) -> dict[int, WorkerStats]:
        """
        Collect latest stats from all workers.

        Non-blocking - drains available stats from queue without waiting.
        Returns most recent stats received from each worker.

        Returns:
            Dict mapping worker_id to WorkerStats
        """
        stats: dict[int, WorkerStats] = {}

        # Drain all available stats from queue
        while True:
            try:
                worker_id, worker_stats = self._stats_queue.get_nowait()
                stats[worker_id] = worker_stats
            except Empty:
                break

        return stats

    def is_healthy(self) -> bool:
        """
        Check if all worker processes are alive.

        Returns:
            True if all workers are running, False if any worker died
        """
        if not self._running:
            return False

        return all(p.is_alive() for p in self._processes)

    def get_alive_count(self) -> int:
        """
        Count of alive worker processes.

        Returns:
            Number of workers currently running
        """
        return sum(1 for p in self._processes if p.is_alive())
