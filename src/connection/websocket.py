import asyncio
import json
import time
from typing import Set

import structlog
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK

from src.connection.stats import ConnectionStats
from src.connection.types import ConnectionStatus, MessageCallback
from src.core.logging import Logger
from src.messages.parser import MessageParser

INITIAL_RECONNECT_DELAY = 1.0
PING_INTERVAL = 10.0
PING_TIMEOUT = 30.0
MAX_RECONNECT_DELAY = 30.0
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

logger: Logger = structlog.get_logger()


class WebsocketConnection:
    """
    Single managed Websocket connection

    Lifecycle:
        1. Create with asset_ids and callback
        2. Call start() to begin connection
        3. Messages flow via callback
        4. Call stop() for graceful shutdown
    """

    __slots__ = (
        "connection_id",
        "parser",
        "asset_ids",
        "on_message",
        "_ws",
        "_status",
        "_stats",
        "_stop_event",
        "_receive_task",
        "_reconnect_delay",
        "_subscribed_asset_ids",
    )

    def __init__(
        self,
        connection_id: str,
        asset_ids: list[str],
        message_parser: MessageParser,
        on_message: MessageCallback,
    ) -> None:
        if len(asset_ids) > 500:
            raise ValueError(
                f"Cannot subscribe to more than 500 markets, got {len(asset_ids)}"
            )

        self.connection_id = connection_id
        self.parser: MessageParser = message_parser
        self.asset_ids = list(asset_ids)  # Copy to prevent mutation
        self.on_message = on_message

        self._ws: ClientConnection | None = None
        self._status: ConnectionStatus = ConnectionStatus.DISCONNECTED
        self._stats: ConnectionStats = ConnectionStats()
        self._stop_event: asyncio.Event = asyncio.Event()
        self._receive_task: asyncio.Task[None] | None = None
        self._reconnect_delay = INITIAL_RECONNECT_DELAY
        self._subscribed_asset_ids: Set[str] = set()

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def stats(self) -> ConnectionStats:
        return self._stats

    @property
    def is_healthy(self) -> bool:
        if self._status != ConnectionStatus.CONNECTED:
            return False

        if self._stats.last_message_ts > 0:
            silence = time.monotonic() - self._stats.last_message_ts
            if silence > 60.0:
                return False

        return True

    def mark_draining(self) -> None:
        """Mark connection as draining (preparing for recycling)."""
        if self._status == ConnectionStatus.CONNECTED:
            self._status = ConnectionStatus.DRAINING

    async def start(self) -> None:
        """Start connection and message processing"""
        if self._status not in (ConnectionStatus.DISCONNECTED, ConnectionStatus.CLOSED):
            return

        self._stop_event.clear()
        self._status = ConnectionStatus.CONNECTING

        # Start connection loop
        self._receive_task: asyncio.Task[None] = asyncio.create_task(
            self._connection_loop(),
            name=f"ws-{self.connection_id}-recv",
        )

    async def stop(self) -> None:
        self._status = ConnectionStatus.CLOSED
        self._stop_event.set()

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _connection_loop(self) -> None:
        """Main connection loop with reconnection logic"""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.exception(
                    f"Connection {self.connection_id} error: {e}",
                    exc_info=True,
                )

            if (
                not self._stop_event.is_set()
                and self._status != ConnectionStatus.CLOSED
            ):
                self._status = ConnectionStatus.DISCONNECTED
                self._stats.reconnect_count += 1

                logger.info(
                    f"Connection {self.connection_id} reconnecting in "
                    f"{self._reconnect_delay:.1f}s (attempt {self._stats.reconnect_count})"
                )

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reconnect_delay,
                    )
                    break
                except asyncio.TimeoutError:
                    pass

                # Exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, MAX_RECONNECT_DELAY
                )

        self._status = ConnectionStatus.CLOSED

    async def _connect_and_run(self) -> None:
        """Establish connection and run message loop"""
        self._status = ConnectionStatus.CONNECTING

        async with connect(
            uri=WS_URL,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
            max_size=10 * 1024 * 1024,  # 10MB max message
        ) as ws:
            self._ws = ws
            await self._send_subscription_message()

            self._status = ConnectionStatus.CONNECTED
            self._reconnect_delay = INITIAL_RECONNECT_DELAY  # Reset backoff

            logger.debug(
                f"Connection {self.connection_id} established, "
                f"subscribed to {len(self.asset_ids)} assets"
            )

            await self._receive_messages(ws)

    async def _send_subscription_message(self) -> None:
        if not self._ws:
            raise RuntimeError(
                "Websocket connection has not been created, cannot send subscription message"
            )

        subscribe_msg = json.dumps({"assets_ids": self.asset_ids, "type": "market"})
        await self._ws.send(subscribe_msg)

        self._subscribed_asset_ids = set(self.asset_ids)

    async def _receive_messages(self, ws: ClientConnection) -> None:
        try:
            while not self._stop_event.is_set():
                message = await ws.recv(decode=False)

                self._track_stats(message)

                try:
                    parsed_messages = self.parser.parse_messages(
                        message,
                    )
                except Exception as e:
                    self._stats.parse_errors += 1
                    logger.exception(
                        f"Connection {self.connection_id} failed to parse message: {e}"
                    )
                    continue

                try:
                    for parsed in parsed_messages:
                        await self.on_message(self.connection_id, parsed)

                except Exception as e:
                    logger.exception(f"Message callback error {e}")

        except ConnectionClosedOK:
            return

    def _track_stats(self, message: bytes) -> None:
        self._stats.last_message_ts = time.monotonic()
        self._stats.messages_received += 1
        self._stats.bytes_received += len(message)
