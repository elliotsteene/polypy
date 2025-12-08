import asyncio
import multiprocessing as mp
import signal
from typing import Any

import structlog

from src.connection.pool import ConnectionPool
from src.core.logging import Logger
from src.lifecycle.controller import LifecycleController
from src.lifecycle.recycler import ConnectionRecycler
from src.messages.parser import MessageParser
from src.registry.asset_entry import AssetStatus
from src.registry.asset_registry import AssetRegistry
from src.router import MessageRouter
from src.server import HTTPServer
from src.worker import WorkerManager

logger: Logger = structlog.getLogger(__name__)


class PolyPy:
    """
    Main application orchestrator.

    Manages lifecycle of all subsystems with proper startup/shutdown order
    """

    __slots__ = (
        "_num_workers",
        "_registry",
        "_workers",
        "_router",
        "_pool",
        "_lifecycle",
        "_recycler",
        "_message_parser",
        "_running",
        "_shutdown_event",
        "_http_server",
        "_enable_http",
        "_http_port",
    )

    def __init__(
        self,
        num_workers: int = 1,
        enable_http: bool = False,
        http_port: int = 8080,
    ) -> None:
        """Initialise application

        Args:
            num_workers: Number of orderbook worker processes (default = 1)
            enable_http: Whether to start HTTP server (default = False)
            http_port: Port for HTTP server (default = 8080)
        """
        self._validate_num_workers(num_workers)
        self._num_workers = num_workers

        # Components (initialised on start)
        self._registry: AssetRegistry | None = None
        self._workers: WorkerManager | None = None
        self._router: MessageRouter | None = None
        self._pool: ConnectionPool | None = None
        self._lifecycle: LifecycleController | None = None
        self._recycler: ConnectionRecycler | None = None
        self._message_parser: MessageParser | None = None

        # HTTP server configuration
        self._enable_http = enable_http
        self._http_port = http_port
        self._http_server: HTTPServer | None = None

        self._running = False
        self._shutdown_event = asyncio.Event()

    def _validate_num_workers(self, num_workers: int) -> None:
        if num_workers < 1:
            raise ValueError("num_workers must be at least 1")

        # Keep 1 core free for the main async process
        max_workers = mp.cpu_count() - 1

        if num_workers > max_workers:
            raise ValueError(f"Max workers is {max_workers}, {num_workers} requested")

    @property
    def is_running(self) -> bool:
        "Whether the application is currently running"
        return self._running

    async def start(self) -> None:
        """
        Start all components in correct order.

        Fast-fails on any component startup error, stopping already-started components

        Raises:
            Exception: If any component fails to start
        """

        if self._running:
            logger.warning("Application already running")
            return

        logger.info("Starting PolyPy application...")

        try:
            # 1. AssetRegistry
            self._registry = AssetRegistry()
            logger.info("✓ AssetRegistry initialised")

            # 2. MessageParser (shared across pool)
            self._message_parser = MessageParser()
            logger.info("✓ MessageParser initialised")

            # 3. WorkerManager
            self._workers = WorkerManager(
                num_workers=self._num_workers,
            )
            worker_queues = self._workers.get_input_queues()
            logger.info(f"✓ WorkerManager initialised with {self._num_workers} workers")

            # 4. MessageRouter
            self._router = MessageRouter(
                num_workers=self._num_workers,
                worker_queues=worker_queues,
            )
            logger.info("✓ MessageRouter initialised")

            # 5. Start WorkerManager
            self._workers.start()
            logger.info(f"✓ WorkerManager started with {self._num_workers} workers")

            # 6. Start MessageRouter
            await self._router.start()
            logger.info("✓ MessageRouter started")

            # 7. ConnectionPool
            self._pool = ConnectionPool(
                registry=self._registry,
                message_callback=self._router.route_message,
                message_parser=self._message_parser,
            )
            logger.info("✓ ConnectionPool created")

            await self._pool.start()
            logger.info("✓ ConnectionPool started")

            # 8. LifecycleController
            self._lifecycle = LifecycleController(
                registry=self._registry,
                on_new_market=self._on_new_market,
                on_market_expired=self._on_market_expired,
            )
            logger.info("✓ LifecycleController created")

            await self._lifecycle.start()
            logger.info("✓ LifecycleController started")

            # 9. ConnectionRecycler
            self._recycler = ConnectionRecycler(
                registry=self._registry,
                pool=self._pool,
            )
            logger.info("✓ ConnectionRecycler created")

            await self._recycler.start()
            logger.info("✓ ConnectionRecycler started")

            # 10. HTTP Server (if enabled)
            if self._enable_http:
                self._http_server = HTTPServer(
                    app=self,  # type: ignore[arg-type]
                    port=self._http_port,
                )
                try:
                    await self._http_server.start()
                    logger.info(f"✓ HTTP server started on port {self._http_port}")
                except Exception as e:
                    logger.error(f"Failed to start HTTP server: {e}")
                    # Don't fail startup if HTTP server fails
                    self._http_server = None

            # 11. Running application
            self._running = True
            logger.info("✓ PolyPy started successfully!")

        except Exception as e:
            logger.error(f"X Application startup failed: {e}")

    async def stop(self) -> None:
        if not self._running:
            logger.warning("Application not running")
            return

        logger.info("Stopping application...")
        start_time = asyncio.get_event_loop().time()

        await self._stop()

        self._running = False

        elapsed_time = asyncio.get_event_loop().time() - start_time

        logger.info(f"Application stopped - shutdown took {elapsed_time:.1f}s")

    async def _stop(self) -> None:
        # Stop in reverse order
        if self._recycler:
            try:
                await self._recycler.stop()
            except Exception as e:
                logger.error(f"Error stopping recycler: {e}")

        # Stop HTTP server (if running)
        if self._http_server:
            try:
                await self._http_server.stop()
            except Exception as e:
                logger.error(f"Error stopping HTTP server: {e}")

        if self._lifecycle:
            try:
                await self._lifecycle.stop()
            except Exception as e:
                logger.error(f"Error stopping _lifecycle: {e}")

        if self._pool:
            try:
                await self._pool.stop()
            except Exception as e:
                logger.error(f"Error stopping _pool: {e}")

        if self._router:
            try:
                await self._router.stop()
            except Exception as e:
                logger.error(f"Error stopping _router: {e}")

        if self._workers:
            try:
                self._workers.stop(timeout=5.0)
            except Exception as e:
                logger.error(f"Error stopping _workers: {e}")

        logger.warning("Cleanup completed")

    async def _cleanup_on_startup_failure(self) -> None:
        logger.warning("Cleaning up after startup failure...")

        await self._stop()

    async def _on_new_market(self, event: str, asset_id: str) -> None:
        logger.debug(f"New market discovered: {asset_id[:16]}...")

    async def _on_market_expired(self, event: str, asset_id: str) -> None:
        logger.debug(f"Market expired: {asset_id[:16]}...")

    async def run(self) -> None:
        """
        Run application with automatic signal handling.

        Blocks until SIGINT or SIGTERM received, then gracefully stops.
        """

        loop = asyncio.get_event_loop()

        def signal_handler(*args, **kwards) -> None:
            logger.info("Shutdown signal received")
            self._shutdown_event.set()

        signals = (signal.SIGINT, signal.SIGTERM)
        for sig in signals:
            loop.add_signal_handler(sig, signal_handler)

        try:
            await self.start()

            await self._shutdown_event.wait()
        finally:
            # Stop all components
            await self.stop()

            for sig in signals:
                try:
                    loop.remove_signal_handler(sig)
                except Exception:
                    pass

    def get_stats(self) -> dict[str, Any]:
        """
        Get comprehensive application statistics.

        Returns:
            dict with keys: running, registry, pool, router, workers, recycler
        """

        stats: dict[str, Any] = {
            "running": self._running,
            "registry": {},
            "pool": {},
            "router": {},
            "workers": {},
            "recycler": {},
        }

        if self._registry:
            stats["registry"] = {
                "total_markets": len(self._registry._assets),
                "pending": self._registry.get_pending_count(),
                "subscribed": len(
                    self._registry.get_by_status(
                        AssetStatus.SUBSCRIBED,
                    )
                ),
                "expired": len(
                    self._registry.get_by_status(
                        AssetStatus.EXPIRED,
                    )
                ),
            }

        if self._pool:
            stats["pool"] = {
                "connection_count": self._pool.connection_count,
                "active_connections": self._pool.active_connection_count,
                "total_capacity": self._pool.get_total_capacity(),
                "stats": self._pool.get_connection_stats(),
            }

        if self._router:
            router_stats = self._router.stats
            stats["router"] = {
                "messages_routed": router_stats.messages_routed,
                "messages_dropped": router_stats.messages_dropped,
                "batches_sent": router_stats.batches_sent,
                "queue_full_events": router_stats.queue_full_events,
                "routing_errors": router_stats.routing_errors,
                "avg_latency_ms": router_stats.avg_latency_ms,
                "queue_depths": self._router.get_queue_depths(),
            }

        if self._workers:
            worker_stats_dict = self._workers.get_stats()
            stats["workers"] = {
                "alive_count": self._workers.get_alive_count(),
                "is_healthy": self._workers.is_healthy(),
                "worker_stats": {
                    worker_id: {
                        "messages_processed": ws.messages_processed,
                        "updates_applied": ws.updates_applied,
                        "snapshots_received": ws.snapshots_received,
                        "avg_processing_time_us": ws.avg_processing_time_us,
                        "orderbook_count": ws.orderbook_count,
                        "memory_usage_mb": ws.memory_usage_bytes / 1024 / 1024,
                    }
                    for worker_id, ws in worker_stats_dict.items()
                },
            }

        if self._lifecycle:
            stats["lifecycle"] = {
                "is_running": self._lifecycle.is_running,
                "known_market_count": self._lifecycle.known_market_count,
            }

        if self._recycler:
            recycler_stats = self._recycler.stats

            stats["recycler"] = {
                "recycles_initiated": recycler_stats.recycles_initiated,
                "recycles_completed": recycler_stats.recycles_completed,
                "recycles_failed": recycler_stats.recycles_failed,
                "success_rate": recycler_stats.success_rate,
                "markets_migrated": recycler_stats.markets_migrated,
                "avg_downtime_ms": recycler_stats.avg_downtime_ms,
                "active_recycles": list(self._recycler.get_active_recycles()),
            }

        return stats

    def is_healthy(self) -> bool:
        if not self._running:
            return False

        if self._workers and not self._workers.is_healthy():
            logger.warning("Health check failed: workers unhealthy")
            return False

        if self._pool and self._pool.active_connection_count == 0:
            logger.debug("Health check: no active connections")

        return True
