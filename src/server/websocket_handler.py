"""WebSocket handler for orderbook streaming.

Follows the async component lifecycle pattern established by LifecycleController
and ConnectionRecycler. Manages multiple concurrent client connections with
independent subscription state.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from aiohttp import WSMessage, web

from src.core.logging import Logger
from src.server.websocket_errors import (
    AssetNotFoundError,
    InvalidMessageError,
    WorkersNotAvailableError,
)

if TYPE_CHECKING:
    from src.app import PolyPy
    from src.worker.protocol import OrderbookResponse

logger: Logger = structlog.getLogger(__name__)


class ClientSubscription:
    """Manages subscription state for a single WebSocket client."""

    __slots__ = ("ws", "asset_id", "stream_task")

    def __init__(self, ws: web.WebSocketResponse) -> None:
        """Initialize client subscription.

        Args:
            ws: WebSocket response object
        """
        self.ws = ws
        self.asset_id: str | None = None
        self.stream_task: asyncio.Task | None = None

    async def cancel_stream(self) -> None:
        """Cancel active streaming task if any."""
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None

    async def close(self) -> None:
        """Close the WebSocket connection and cancel streaming."""
        await self.cancel_stream()
        if not self.ws.closed:
            await self.ws.close()


class OrderbookStreamHandler:
    """
    Handles WebSocket connections for orderbook streaming.

    Manages multiple concurrent clients, each with independent subscription state.
    Follows the async component lifecycle pattern used throughout the codebase.
    """

    __slots__ = ("_app", "_clients")

    def __init__(self, app: "PolyPy") -> None:
        """Initialize orderbook stream handler.

        Args:
            app: PolyPy application instance for orderbook queries
        """
        self._app = app
        self._clients: dict[web.WebSocketResponse, ClientSubscription] = {}

    async def handle_connection(self, request: web.Request) -> web.WebSocketResponse:
        """Handle new WebSocket connection.

        Args:
            request: HTTP request to upgrade to WebSocket

        Returns:
            WebSocketResponse for the connection
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client = ClientSubscription(ws)
        self._clients[ws] = client

        logger.info(f"WebSocket client connected (total: {len(self._clients)})")

        try:
            await self._handle_messages(client)
        finally:
            await client.close()
            self._clients.pop(ws, None)
            logger.info(
                f"WebSocket client disconnected (remaining: {len(self._clients)})"
            )

        return ws

    async def _handle_messages(self, client: ClientSubscription) -> None:
        """Handle incoming WebSocket messages from a client.

        Args:
            client: Client subscription to handle messages for
        """
        async for msg in client.ws:
            if msg.type == web.WSMsgType.TEXT:
                await self._process_text_message(client, msg)
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {client.ws.exception()}")
                break

    async def _process_text_message(
        self, client: ClientSubscription, msg: WSMessage
    ) -> None:
        """Process a text message from the client.

        Args:
            client: Client subscription
            msg: WebSocket message
        """
        try:
            data = msg.json()

            if "subscribe" in data:
                await self._handle_subscribe(client, data)
            elif "unsubscribe" in data:
                await self._handle_unsubscribe(client)
            else:
                raise InvalidMessageError(
                    "Message must contain 'subscribe' or 'unsubscribe'"
                )

        except InvalidMessageError as e:
            await client.ws.send_json({"type": "error", "error": str(e)})
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            await client.ws.send_json({"type": "error", "error": str(e)})

    async def _handle_subscribe(self, client: ClientSubscription, data: dict) -> None:
        """Handle subscribe message from client.

        Args:
            client: Client subscription
            data: Parsed JSON data containing 'subscribe' and optional 'interval_ms'
        """
        # Cancel existing stream if any
        await client.cancel_stream()

        asset_id = data["subscribe"]
        interval = data.get("interval_ms", 500) / 1000.0

        client.asset_id = asset_id
        client.stream_task = asyncio.create_task(
            self._stream_orderbook(client, asset_id, interval)
        )

    async def _handle_unsubscribe(self, client: ClientSubscription) -> None:
        """Handle unsubscribe message from client.

        Args:
            client: Client subscription
        """
        await client.cancel_stream()
        client.asset_id = None

    async def _stream_orderbook(
        self,
        client: ClientSubscription,
        asset_id: str,
        interval: float,
    ) -> None:
        """Stream orderbook updates to client at specified interval.

        Args:
            client: Client subscription
            asset_id: Asset to stream orderbook for
            interval: Update interval in seconds
        """
        try:
            # Validate workers are available
            if not self._app._workers or not self._app._router:
                raise WorkersNotAvailableError("Workers not available")

            worker_idx = self._app._router.get_worker_for_asset(asset_id)

            while True:
                response = await self._app._workers.query_orderbook(
                    asset_id=asset_id,
                    worker_idx=worker_idx,
                    depth=10,
                )

                if response.found:
                    message = self._format_orderbook_message(response)
                    await client.ws.send_json(message)
                else:
                    raise AssetNotFoundError(response.error or "Asset not found")

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            # Normal cancellation when unsubscribing
            pass
        except (WorkersNotAvailableError, AssetNotFoundError) as e:
            logger.error(f"Stream error for {asset_id}: {e}")
            await client.ws.send_json({"type": "error", "error": str(e)})
        except Exception as e:
            logger.exception(f"Unexpected stream error for {asset_id}: {e}")
            await client.ws.send_json({"type": "error", "error": str(e)})

    def _format_orderbook_message(self, response: "OrderbookResponse") -> dict:
        """Format orderbook response into WebSocket message.

        Args:
            response: Orderbook query response

        Returns:
            Dictionary ready for JSON serialization
        """
        return {
            "type": "orderbook",
            "asset_id": response.asset_id,
            "bids": response.bids,
            "asks": response.asks,
            "metrics": (
                {
                    "spread": response.metrics.spread,
                    "mid_price": response.metrics.mid_price,
                    "imbalance": response.metrics.imbalance,
                }
                if response.metrics
                else None
            ),
            "last_update_ts": response.last_update_ts,
        }

    def get_client_count(self) -> int:
        """Get number of connected clients.

        Returns:
            Number of active WebSocket connections
        """
        return len(self._clients)

    def get_subscribed_count(self) -> int:
        """Get number of clients with active subscriptions.

        Returns:
            Number of clients currently streaming orderbooks
        """
        return sum(
            1 for client in self._clients.values() if client.asset_id is not None
        )
