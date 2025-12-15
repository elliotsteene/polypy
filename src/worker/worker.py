"""
Worker Process Manager - Manages CPU-intensive orderbook workers.

Each worker:
- Runs in separate OS process (bypasses GIL)
- Owns subset of orderbooks (determined by consistent hash in router)
- Processes messages from dedicated multiprocessing queue
- Reports health and stats periodically
"""

import signal
import time
from multiprocessing import Queue as MPQueue
from multiprocessing.synchronize import Event
from queue import Empty, Full
from typing import assert_never

import structlog

from src.core.logging import Logger
from src.messages.protocol import EventType, ParsedMessage, unscale_price, unscale_size
from src.orderbook.orderbook_store import Asset, OrderbookStore
from src.worker.protocol import OrderbookMetrics, OrderbookRequest, OrderbookResponse
from src.worker.stats import WorkerStats

logger: Logger = structlog.getLogger(__name__)

# Configuration constants
QUEUE_TIMEOUT: float = 0.1  # 100ms timeout for queue.get()
HEARTBEAT_INTERVAL: float = 5.0  # Seconds between heartbeat updates
STATS_INTERVAL: float = 30.0  # Seconds between stats reports


class ShutdownReceivedError(Exception):
    pass


class Worker:
    def __init__(
        self,
        worker_id: int,
        input_queue: MPQueue,
        stats_queue: MPQueue,
        response_queue: MPQueue,
        orderbook_store: OrderbookStore,
        stats: WorkerStats,
    ) -> None:
        self._worker_id = worker_id
        self._input_queue = input_queue
        self._stats_queue = stats_queue
        self._response_queue = response_queue
        self._store = orderbook_store
        self._stats = stats

    def run(self, shutdown_event: Event) -> None:
        # Initialize timers
        last_heartbeat = time.monotonic()
        last_stats_report = time.monotonic()

        logger.info(f"Worker {self._worker_id} started")

        try:
            while not shutdown_event.is_set():
                try:
                    self._process_message_loop()

                except ShutdownReceivedError:
                    break

                except Empty:
                    # Timeout - check shutdown_event and continue
                    pass

                except Exception as e:
                    logger.exception(
                        f"Worker {self._worker_id} error processing message: {e}"
                    )
                    # Continue processing despite errors

                # Periodic heartbeat - update orderbook and memory stats
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    self._update_stats()
                    last_heartbeat = now

                # Periodic stats report - send to parent process
                if now - last_stats_report >= STATS_INTERVAL:
                    self._put_stats()
                    last_stats_report = now

        except Exception as e:
            logger.exception(f"Worker {self._worker_id} fatal error: {e}")

        finally:
            self._send_final_stats()

    def _process_message_loop(self) -> None:
        # Get message with timeout (prevents infinite blocking)
        item = self._input_queue.get(timeout=QUEUE_TIMEOUT)

        if item is None:
            # None is shutdown sentinel from router.stop()
            logger.info(f"Worker {self._worker_id} received shutdown sentinel")
            raise ShutdownReceivedError("Shutdown sentinel received")

        # Unpack message and timestamp
        message, receive_ts = item

        if isinstance(message, OrderbookRequest):
            self._handle_orderbook_request(message)
            return

        # Handle normal message processing
        process_start = time.monotonic()

        # Process the message
        self._process_message(message)

        # Track processing time
        process_time_ms = (time.monotonic() - process_start) * 1000
        self._stats.processing_time_ms += process_time_ms
        self._stats.last_message_ts = time.monotonic()

    def _send_final_stats(self) -> None:
        self._update_stats()
        self._put_stats()

    def _update_stats(self) -> None:
        self._stats.orderbook_count = len(self._store._books)
        self._stats.memory_usage_bytes = self._store.memory_usage()

    def _put_stats(self) -> None:
        try:
            self._stats_queue.put_nowait((self._worker_id, self._stats))
        except Full:
            pass

    def _process_message(
        self,
        message: ParsedMessage,
    ) -> None:
        """
        Process single message and update orderbook state.

        Args:
            store: OrderbookStore instance for this worker
            message: Parsed message to process
            stats: Stats tracker to update
        """
        self._stats.messages_processed += 1

        try:
            match message.event_type:
                case EventType.BOOK:
                    self._process_book_snapshot(message)

                case EventType.PRICE_CHANGE:
                    self._process_price_change(message)

                case EventType.LAST_TRADE_PRICE:
                    self._process_last_trade(message)

                case EventType.TICK_SIZE_CHANGE:
                    self._process_tick_size_change(message)

                case EventType.UNKNOWN:
                    logger.warning(f"Unknown event type: {message.event_type}")
                case _:
                    assert_never(message.event_type)

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
        self,
        message: ParsedMessage,
    ) -> None:
        """Process BOOK snapshot message."""
        assert message.book is not None, "BOOK message missing book field"

        asset = Asset(asset_id=message.asset_id, market=message.market)
        orderbook = self._store.register_asset(asset)
        orderbook.apply_snapshot(message.book, message.raw_timestamp)

        self._stats.snapshots_received += 1
        self._stats.updates_applied += 1

    def _process_price_change(
        self,
        message: ParsedMessage,
    ) -> None:
        """Process PRICE_CHANGE message."""
        assert message.price_change is not None, (
            "PRICE_CHANGE message missing price_change field"
        )

        orderbook = self._store.get_state(message.asset_id)
        if not orderbook:
            logger.warning(
                f"Skipping price_change for unknown asset {message.asset_id}"
            )
            return

        orderbook.apply_price_change(message.price_change, message.raw_timestamp)
        self._stats.updates_applied += 1

    def _process_last_trade(
        self,
        message: ParsedMessage,
    ) -> None:
        """Process LAST_TRADE_PRICE message."""
        assert message.last_trade is not None, (
            "LAST_TRADE_PRICE message missing last_trade field"
        )

        # No state update for trades in v1 (handler_factory deferred)
        # In future, custom handlers would receive callback here
        pass

    def _process_tick_size_change(
        self,
        message: ParsedMessage,
    ) -> None:
        """Process TICK_SIZE_CHANGE message."""
        assert message.tick_size_change is not None, (
            "TICK_SIZE_CHANGE message missing last_trade field"
        )

        # No state update for trades in v1 (handler_factory deferred)
        # In future, custom handlers would receive callback here
        pass

    def _handle_orderbook_request(self, request: "OrderbookRequest") -> None:
        """Handle orderbook query request."""
        orderbook = self._store.get_state(request.asset_id)

        if not orderbook:
            response = OrderbookResponse(
                request_id=request.request_id,
                asset_id=request.asset_id,
                found=False,
                error="Asset not found",
            )
        else:
            # Unscale prices and sizes for API response
            bids = [
                (unscale_price(level.price), unscale_size(level.size))
                for level in orderbook.get_bids(request.depth)
            ]
            asks = [
                (unscale_price(level.price), unscale_size(level.size))
                for level in orderbook.get_asks(request.depth)
            ]

            # Calculate imbalance using unscaled sizes
            bid_vol = sum(size for _, size in bids)
            ask_vol = sum(size for _, size in asks)
            total_vol = bid_vol + ask_vol
            imbalance = bid_vol / total_vol if total_vol > 0 else 0.5

            # Unscale metrics
            metrics = OrderbookMetrics(
                spread=unscale_price(orderbook.spread) if orderbook.spread else None,
                mid_price=unscale_price(orderbook.mid_price)
                if orderbook.mid_price
                else None,
                imbalance=imbalance,
            )

            response = OrderbookResponse(
                request_id=request.request_id,
                asset_id=request.asset_id,
                found=True,
                bids=bids,
                asks=asks,
                metrics=metrics,
                last_update_ts=orderbook.last_update_ts,
            )

        try:
            self._response_queue.put_nowait(response)
        except Full:
            logger.warning(
                f"Response queue full, dropping response for {request.asset_id}"
            )


def _worker_process(
    worker_id: int,
    input_queue: MPQueue,
    stats_queue: MPQueue,
    response_queue: MPQueue,
    shutdown_event: Event,
) -> None:
    """
    Worker process entry point with health monitoring.

    Runs in separate process - must be pickleable (module-level function).

    Args:
        worker_id: Unique identifier for this worker (0-indexed)
        input_queue: Queue to receive (ParsedMessage, timestamp) tuples
        stats_queue: Queue to send periodic stats updates
        response_queue: Queue to send orderbook query responses
        shutdown_event: Multiprocessing event for shutdown coordination
    """
    # Ignore SIGINT - parent process handles graceful shutdown
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Initialize worker state
    store = OrderbookStore()
    stats = WorkerStats()

    # Create worker
    worker = Worker(
        worker_id=worker_id,
        input_queue=input_queue,
        stats_queue=stats_queue,
        response_queue=response_queue,
        orderbook_store=store,
        stats=stats,
    )

    worker.run(shutdown_event=shutdown_event)

    logger.info(
        f"Worker {worker_id} stopping. "
        f"Processed {worker._stats.messages_processed} messages, "
        f"managing {worker._stats.orderbook_count} orderbooks, "
        f"memory: {worker._stats.memory_usage_bytes / 1024 / 1024:.2f}MB"
    )
