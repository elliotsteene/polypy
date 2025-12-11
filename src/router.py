"""
Async Message Router - Bridges async domain to multiprocessing workers.

Routes ParsedMessage from WebSocket connections to worker processes via
multiprocessing queues using consistent hashing for worker assignment.
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from multiprocessing import Queue as MPQueue
from queue import Full
from typing import TYPE_CHECKING, Final

import structlog

from src.core.logging import Logger

if TYPE_CHECKING:
    from src.messages.protocol import ParsedMessage

    # Type alias for worker queue (will be consumed by ENG-003)
    WorkerQueue = MPQueue[tuple[ParsedMessage, float] | None]
else:
    from src.messages.protocol import ParsedMessage

    # At runtime, just use the base class without subscripting
    WorkerQueue = MPQueue

logger: Logger = structlog.getLogger(__name__)

# Configuration constants
ASYNC_QUEUE_SIZE: Final[int] = 20_000  # Max pending in async queue
WORKER_QUEUE_SIZE: Final[int] = 5_000  # Max pending per worker
BATCH_SIZE: Final[int] = 100  # Messages to batch before routing
BATCH_TIMEOUT: Final[float] = 0.01  # 10ms max wait for batch
PUT_TIMEOUT: Final[float] = 0.001  # 1ms timeout for queue put


@dataclass(slots=True)
class RouterStats:
    """Routing performance metrics."""

    messages_routed: int = 0
    messages_dropped: int = 0
    batches_sent: int = 0
    queue_full_events: int = 0
    routing_errors: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        """Average routing latency in milliseconds."""
        if self.messages_routed > 0:
            return self.total_latency_ms / self.messages_routed
        return 0.0


def _hash_to_worker(asset_id: str, num_workers: int) -> int:
    """
    Consistent hash of asset_id to worker index.

    Uses MD5 for speed (not security-sensitive). The same asset_id
    will always hash to the same worker index.

    Args:
        asset_id: The asset identifier to hash
        num_workers: Number of workers to distribute across

    Returns:
        Worker index in range [0, num_workers)
    """
    hash_bytes = hashlib.md5(asset_id.encode()).digest()
    hash_int = int.from_bytes(hash_bytes[:8], "little")
    return hash_int % num_workers


class MessageRouter:
    """
    Routes messages from async domain to worker processes.

    Usage:
        router = MessageRouter(num_workers=4)
        queues = router.get_worker_queues()  # Pass to worker processes
        await router.start()

        # In connection callback:
        await router.route_message(connection_id, message)
    """

    __slots__ = (
        "_num_workers",
        "_worker_queues",
        "_async_queue",
        "_routing_task",
        "_running",
        "_stats",
        "_asset_worker_cache",
    )

    def __init__(
        self,
        num_workers: int,
        worker_queues: list[WorkerQueue],
    ) -> None:
        """
        Initialize router.

        Args:
            num_workers: Number of worker processes to route to
            worker_queues: Pre-created queues from WorkerManager.
                          Must have exactly one queue per worker.

        Raises:
            ValueError: If num_workers < 1 or queue count doesn't match num_workers
        """
        if num_workers < 1:
            raise ValueError("num_workers must be at least 1")

        if len(worker_queues) != num_workers:
            raise ValueError(
                f"worker_queues length ({len(worker_queues)}) must match "
                f"num_workers ({num_workers})"
            )

        self._num_workers = num_workers
        self._worker_queues = worker_queues

        self._async_queue: asyncio.Queue[tuple[str, ParsedMessage, float]] = (
            asyncio.Queue(maxsize=ASYNC_QUEUE_SIZE)
        )
        self._routing_task: asyncio.Task[None] | None = None
        self._running = False
        self._stats = RouterStats()
        self._asset_worker_cache: dict[str, int] = {}

    @property
    def stats(self) -> RouterStats:
        """Get current routing statistics."""
        return self._stats

    @property
    def num_workers(self) -> int:
        """Number of worker processes."""
        return self._num_workers

    def get_worker_queues(self) -> list[WorkerQueue]:
        """Get queues to pass to worker processes."""
        return self._worker_queues

    def get_worker_for_asset(self, asset_id: str) -> int:
        """Get worker index for an asset (cached)."""
        if asset_id not in self._asset_worker_cache:
            self._asset_worker_cache[asset_id] = _hash_to_worker(
                asset_id, self._num_workers
            )
        return self._asset_worker_cache[asset_id]

    def get_queue_depths(self) -> dict[str, int]:
        """Get current queue depths for monitoring."""
        depths = {"async_queue": self._async_queue.qsize()}

        # Note: qsize() on multiprocessing.Queue raises NotImplementedError on macOS
        for i, q in enumerate(self._worker_queues):
            try:
                depths[f"worker_{i}"] = q.qsize()
            except NotImplementedError:
                depths[f"worker_{i}"] = -1  # -1 indicates unavailable

        return depths

    async def route_message(
        self,
        connection_id: str,
        message: ParsedMessage,
    ) -> bool:
        """
        Route a message to appropriate worker.

        Called from connection callbacks. This is the entry point
        for all messages from WebSocket connections.

        Args:
            connection_id: ID of the connection that received the message
            message: Parsed message to route

        Returns:
            True if queued successfully, False if dropped due to backpressure
        """
        try:
            # Add receive timestamp for latency tracking
            receive_ts = time.monotonic()
            self._async_queue.put_nowait((connection_id, message, receive_ts))
            return True
        except asyncio.QueueFull:
            self._stats.messages_dropped += 1
            logger.warning(
                f"Async queue full, message dropped for asset {message.asset_id}"
            )
            return False

    async def _routing_loop(self) -> None:
        """
        Main routing loop - batches messages and routes to workers.

        Collects messages up to BATCH_SIZE or BATCH_TIMEOUT, then
        groups by worker and sends to multiprocessing queues.
        """
        batch: list[tuple[str, ParsedMessage, float]] = []

        while self._running:
            try:
                # Collect batch
                batch.clear()
                deadline = time.monotonic() + BATCH_TIMEOUT

                while len(batch) < BATCH_SIZE:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break

                    try:
                        item = await asyncio.wait_for(
                            self._async_queue.get(),
                            timeout=remaining,
                        )
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break

                if batch:
                    await self._route_batch(batch)

            except asyncio.CancelledError:
                # Drain remaining messages before exit
                while not self._async_queue.empty():
                    try:
                        item = self._async_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break
                if batch:
                    await self._route_batch(batch)
                break
            except Exception as e:
                logger.error(f"Routing loop error: {e}", exc_info=True)
                self._stats.routing_errors += 1

    async def _route_batch(
        self,
        batch: list[tuple[str, ParsedMessage, float]],
    ) -> None:
        """
        Route batch of messages to workers.

        Groups messages by worker for efficiency, then sends to
        multiprocessing queues with non-blocking puts.
        """
        # Group by worker
        worker_batches: dict[int, list[tuple[ParsedMessage, float]]] = {
            i: [] for i in range(self._num_workers)
        }

        for connection_id, message, receive_ts in batch:
            worker_idx = self.get_worker_for_asset(message.asset_id)
            worker_batches[worker_idx].append((message, receive_ts))

        # Send to workers
        now = time.monotonic()

        for worker_idx, messages in worker_batches.items():
            if not messages:
                continue

            queue = self._worker_queues[worker_idx]

            for message, receive_ts in messages:
                try:
                    # Put with timeout - non-blocking to prevent router stall
                    queue.put((message, receive_ts), timeout=PUT_TIMEOUT)

                    self._stats.messages_routed += 1
                    self._stats.total_latency_ms += (now - receive_ts) * 1000

                except Full:
                    self._stats.queue_full_events += 1
                    self._stats.messages_dropped += 1
                    logger.warning(f"Worker {worker_idx} queue full, message dropped")

        self._stats.batches_sent += 1

    async def start(self) -> None:
        """Start the routing task."""
        if self._running:
            logger.warning("Router already running")
            return

        self._running = True
        self._routing_task = asyncio.create_task(
            self._routing_loop(),
            name="message-router",
        )
        logger.info(f"Message router started with {self._num_workers} workers")

    async def stop(self) -> None:
        """Stop routing and cleanup."""
        if not self._running:
            return

        self._running = False

        if self._routing_task:
            self._routing_task.cancel()
            try:
                await self._routing_task
            except asyncio.CancelledError:
                pass

        # Signal workers to stop (send None sentinel)
        for i, q in enumerate(self._worker_queues):
            try:
                q.put_nowait(None)
            except Full:
                logger.warning(f"Could not send shutdown sentinel to worker {i}")

        logger.info(
            f"Message router stopped. Stats: "
            f"routed={self._stats.messages_routed}, "
            f"dropped={self._stats.messages_dropped}, "
            f"avg_latency={self._stats.avg_latency_ms:.2f}ms"
        )
