import asyncio
import os
import time
import uuid
from multiprocessing import Event as MPEvent
from multiprocessing import Process
from multiprocessing import Queue as MPQueue
from multiprocessing.synchronize import Event
from queue import Empty, Full

import structlog

from src.core.logging import Logger
from src.worker.protocol import OrderbookRequest, OrderbookResponse
from src.worker.stats import WorkerStats
from src.worker.worker import _worker_process

logger: Logger = structlog.getLogger(__name__)


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
        "_response_queue",
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
        self._response_queue: MPQueue = MPQueue()
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
                    self._response_queue,
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

    def get_response_queue(self) -> MPQueue:
        """Get the shared response queue for worker responses."""
        return self._response_queue

    async def query_orderbook(
        self,
        asset_id: str,
        worker_idx: int,
        depth: int = 10,
        timeout: float = 1.0,
    ) -> "OrderbookResponse":
        """
        Query orderbook state from a worker.

        Args:
            asset_id: Asset to query
            worker_idx: Worker index that owns this asset
            depth: Number of price levels to return
            timeout: Max wait time for response

        Returns:
            OrderbookResponse with orderbook state or error
        """
        request_id = str(uuid.uuid4())
        request = OrderbookRequest(
            request_id=request_id,
            asset_id=asset_id,
            depth=depth,
        )

        # Send request to worker
        queue = self._input_queues[worker_idx]
        try:
            queue.put((request, time.monotonic()), timeout=0.1)
        except Full:
            return OrderbookResponse(
                request_id=request_id,
                asset_id=asset_id,
                found=False,
                error="Worker queue full",
            )

        # Wait for response (poll response queue)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            # Yield control to event loop while polling
            await asyncio.sleep(0.01)

            try:
                response = self._response_queue.get_nowait()
                if response.request_id == request_id:
                    return response
                # Wrong response - put back (for concurrent requests)
                # For single client, we can discard, but being defensive
                try:
                    self._response_queue.put_nowait(response)
                except Full:
                    logger.warning(
                        f"Response queue full, discarding response for {response.request_id}"
                    )
            except Empty:
                continue

        return OrderbookResponse(
            request_id=request_id,
            asset_id=asset_id,
            found=False,
            error="Request timeout",
        )
