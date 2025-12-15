from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from aiohttp import web
from aiohttp.hdrs import CONTENT_TYPE
from prometheus_client import CONTENT_TYPE_LATEST

from src.core.logging import Logger
from src.registry.asset_entry import AssetStatus

if TYPE_CHECKING:
    from src.app import PolyPy

logger: Logger = structlog.getLogger(__name__)


class HTTPServer:
    """
    HTTP server exposing health and stats endpoints.

    Follows async component lifecycle pattern established by LifecycleController
    and ConnectionRecycler.
    """

    __slots__ = (
        "_app",
        "_port",
        "_host",
        "_runner",
        "_site",
        "_running",
    )

    def __init__(
        self,
        app: "PolyPy",
        port: int = 8080,
        host: str = "0.0.0.0",
    ) -> None:
        """Initialize HTTP server.

        Args:
            app: PolyPy application instance to query for health/stats
            port: Port to bind to (default: 8080)
            host: Host to bind to (default: 0.0.0.0 for container compatibility)
        """
        self._app = app
        self._port = port
        self._host = host
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False

    async def start(self) -> None:
        """Start HTTP server.

        Raises:
            Exception: If server fails to start (port conflict, etc.)
        """
        if self._running:
            logger.warning("HTTP server already running")
            return

        logger.info(f"Starting HTTP server on {self._host}:{self._port}")

        # Create aiohttp application with routes
        web_app = web.Application()
        web_app.router.add_get("/health", self._handle_health)
        web_app.router.add_get("/stats", self._handle_stats)
        web_app.router.add_get("/metrics", self._handle_metrics)
        web_app.router.add_get("/markets", self._handle_markets)
        web_app.router.add_get("/orderbook/{asset_id}", self._handle_orderbook)

        # Start server
        self._runner = web.AppRunner(web_app)
        await self._runner.setup()

        self._site = web.TCPSite(
            self._runner,
            self._host,
            self._port,
        )
        await self._site.start()

        self._running = True
        logger.info(f"✓ HTTP server started on {self._host}:{self._port}")

    async def stop(self) -> None:
        """Stop HTTP server gracefully."""
        if not self._running:
            logger.warning("HTTP server not running")
            return

        logger.info("Stopping HTTP server...")

        self._running = False

        # Stop accepting new connections
        if self._site:
            await self._site.stop()
            self._site = None

        # Cleanup runner
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        logger.info("✓ HTTP server stopped")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle GET /health endpoint.

        Returns:
            200 OK with health status and component breakdown when healthy
            503 Service Unavailable when unhealthy
        """
        logger.debug("GET /health")

        # Get overall health
        is_healthy = self._app.is_healthy()

        # Build component health breakdown
        components = {
            "registry": self._app._registry is not None,
            "pool": (
                self._app._pool is not None
                and self._app._pool.active_connection_count > 0
            ),
            "router": self._app._router is not None,
            "workers": (
                self._app._workers is not None and self._app._workers.is_healthy()
            ),
            "lifecycle": (
                self._app._lifecycle is not None and self._app._lifecycle.is_running
            ),
            "recycler": (
                self._app._recycler is not None and self._app._recycler.is_running
            ),
        }

        response_data = {
            "healthy": is_healthy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": components,
        }

        status = 200 if is_healthy else 503
        return web.json_response(response_data, status=status)

    async def _handle_stats(self, request: web.Request) -> web.Response:
        """Handle GET /stats endpoint.

        Returns:
            200 OK with comprehensive statistics
        """
        logger.debug("GET /stats")

        stats = self._app.get_stats()
        return web.json_response(stats, status=200)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Handle GET /metrics endpoint.

        Returns:
            200 OK with Prometheus text exposition format
        """
        try:
            # Import here to avoid circular dependency
            from src.metrics.prometheus import MetricsCollector

            # Collect and generate metrics
            collector = MetricsCollector(self._app)
            metrics_bytes = collector.collect_metrics()

        except Exception as e:
            logger.exception(f"Error getting metrics: {e}")
            return web.json_response({"error": e}, status=500)

        rsp = web.Response(
            body=metrics_bytes,
            headers={CONTENT_TYPE: CONTENT_TYPE_LATEST},
        )

        return rsp

    async def _handle_markets(self, request: web.Request) -> web.Response:
        """Handle GET /markets endpoint.

        Returns list of subscribed markets with metadata.

        Returns:
            200 OK with market list
            503 Service Unavailable when lifecycle controller not available
        """
        logger.debug("GET /markets")

        if not self._app._lifecycle:
            return web.json_response(
                {"error": "Lifecycle controller not available"}, status=503
            )

        markets = self._app._lifecycle.get_all_markets()
        subscribed_assets = (
            self._app._registry.get_by_status(AssetStatus.SUBSCRIBED)
            if self._app._registry
            else frozenset()
        )

        result = []
        for market in markets:
            # Check if any token from this market is subscribed
            token_ids = [t.get("token_id", "") for t in market.tokens]
            if any(tid in subscribed_assets for tid in token_ids):
                result.append(
                    {
                        "condition_id": market.condition_id,
                        "question": market.question,
                        "outcomes": market.outcomes,
                        "tokens": market.tokens,
                        "end_date_iso": market.end_date_iso,
                        "end_timestamp": market.end_timestamp,
                    }
                )

        return web.json_response({"markets": result}, status=200)

    async def _handle_orderbook(self, request: web.Request) -> web.Response:
        """Handle GET /orderbook/{asset_id} endpoint.

        Returns:
            200 OK with orderbook data
            404 Not Found if asset not found
            503 Service Unavailable when workers not available
        """
        asset_id = request.match_info["asset_id"]
        depth = int(request.query.get("depth", "10"))

        logger.debug(f"GET /orderbook/{asset_id}")

        if not self._app._workers or not self._app._router:
            return web.json_response({"error": "Workers not available"}, status=503)

        # Determine which worker owns this asset
        worker_idx = self._app._router.get_worker_for_asset(asset_id)

        # Query worker
        response = await self._app._workers.query_orderbook(
            asset_id=asset_id,
            worker_idx=worker_idx,
            depth=depth,
        )

        if not response.found:
            return web.json_response(
                {"error": response.error or "Asset not found"},
                status=404,
            )

        return web.json_response(
            asdict(response),
            status=200,
        )
