"""
Connection Pool Manager - Orchestrates multiple WebSocket connections.

Responsibilities:
- Create/destroy connections based on demand
- Track capacity and pollution per connection
- Assign markets to appropriate connections
- Coordinate recycling of polluted connections
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

import structlog

from src.connection.types import ConnectionStatus, MessageCallback
from src.connection.websocket import WebsocketConnection
from src.core.logging import Logger
from src.lifecycle.recycler import ConnectionRecycler, RecycleStats
from src.messages.parser import MessageParser
from src.registry.asset_registry import AssetRegistry

logger: Logger = structlog.get_logger()

# Configuration
TARGET_MARKETS_PER_CONNECTION = 400  # Leave headroom below 500 limit
MAX_MARKETS_PER_CONNECTION = 500
POLLUTION_THRESHOLD = 0.30  # 30% expired triggers recycling
MIN_AGE_FOR_RECYCLING = 300.0  # Don't recycle connections younger than 5 min
BATCH_SUBSCRIPTION_INTERVAL = 30.0  # Check for pending markets every 30s
MIN_PENDING_FOR_NEW_CONNECTION = 50  # Min pending to justify new connection


@dataclass(slots=True)
class ConnectionInfo:
    """Metadata about a managed connection."""

    connection: WebsocketConnection
    created_at: float = field(default_factory=time.monotonic)
    is_draining: bool = False

    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at


class ConnectionPool:
    """
    Manages pool of WebSocket connections.

    Thread-safety: All methods are async and safe for concurrent calls
    from the same event loop.
    """

    __slots__ = (
        "_registry",
        "_message_callback",
        "_message_parser",
        "_connections",
        "_subscription_task",
        "_recycler",
        "_running",
        "_lock",
    )

    def __init__(
        self,
        registry: AssetRegistry,
        message_callback: MessageCallback,
        message_parser: MessageParser | None = None,
    ) -> None:
        """
        Initialize pool.

        Args:
            registry: Market registry for coordination
            message_callback: Callback for all received messages
            message_parser: Optional parser instance (creates one if not provided)
        """
        self._registry = registry
        self._message_callback = message_callback
        self._message_parser = message_parser or MessageParser()
        self._connections: dict[str, ConnectionInfo] = {}
        self._subscription_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

        # Initialize recycler
        self._recycler = ConnectionRecycler(registry, self)

    @property
    def connection_count(self) -> int:
        """Total number of connections (including draining)."""
        return len(self._connections)

    @property
    def active_connection_count(self) -> int:
        """Number of healthy, non-draining connections."""
        return sum(
            1
            for info in self._connections.values()
            if info.connection.status == ConnectionStatus.CONNECTED
            and not info.is_draining
        )

    def get_total_capacity(self) -> int:
        """Total subscription slots available across all non-draining connections."""
        total = 0
        for cid, info in self._connections.items():
            if info.is_draining:
                continue

            used = len(self._registry.get_by_connection(cid))
            available = TARGET_MARKETS_PER_CONNECTION - used
            total += max(0, available)

        return total

    async def start(self) -> None:
        """Start the pool and subscription manager."""
        if self._running:
            logger.warning("Connection pool already running")
            return

        self._running = True

        # Start subscription management task
        self._subscription_task = asyncio.create_task(
            self._subscription_loop(), name="pool-subscription-manager"
        )

        # Start recycler
        await self._recycler.start()

        logger.info("Connection pool started")

    async def stop(self) -> None:
        """Stop all connections and cleanup."""
        if not self._running:
            return

        self._running = False

        # Stop recycler first (before stopping connections)
        await self._recycler.stop()

        # Stop subscription task
        if self._subscription_task:
            self._subscription_task.cancel()
            try:
                await self._subscription_task
            except asyncio.CancelledError:
                pass

        # Stop all connections concurrently
        stop_tasks = [info.connection.stop() for info in self._connections.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        self._connections.clear()
        logger.info("Connection pool stopped")

    async def _subscription_loop(self) -> None:
        """Periodically process pending markets."""
        while self._running:
            try:
                await asyncio.sleep(BATCH_SUBSCRIPTION_INTERVAL)

                if not self._running:
                    break

                await self._process_pending_markets()
                # Connection recycling is now handled by ConnectionRecycler

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Subscription loop error: {e}", exc_info=True)

    async def _process_pending_markets(self) -> None:
        """Create connections for pending markets."""
        pending_count = self._registry.get_pending_count()

        if pending_count < MIN_PENDING_FOR_NEW_CONNECTION:
            logger.debug(
                f"Skipping connection creation: {pending_count} pending "
                f"(threshold: {MIN_PENDING_FOR_NEW_CONNECTION})"
            )
            return

        async with self._lock:
            # Take batch of pending markets
            batch = await self._registry.take_pending_batch(
                TARGET_MARKETS_PER_CONNECTION
            )

            if not batch:
                return

            # Create new connection
            connection_id = f"conn-{uuid.uuid4().hex[:8]}"
            connection = WebsocketConnection(
                connection_id=connection_id,
                asset_ids=batch,
                message_parser=self._message_parser,
                on_message=self._message_callback,
            )

            self._connections[connection_id] = ConnectionInfo(connection=connection)

            # Start connection
            await connection.start()

            # Mark markets as subscribed
            await self._registry.mark_subscribed(batch, connection_id)

            logger.info(
                f"Created connection {connection_id} with {len(batch)} markets "
                f"(total connections: {len(self._connections)}, "
                f"pending remaining: {self._registry.get_pending_count()})"
            )

    async def _check_for_recycling(self) -> None:
        """Check connections for recycling needs."""
        for connection_id, info in list(self._connections.items()):
            # Skip connections already draining
            if info.is_draining:
                continue

            # Skip young connections (don't recycle if < 5 min old)
            if info.age < MIN_AGE_FOR_RECYCLING:
                continue

            # Get stats from registry
            stats = self._registry.connection_stats(connection_id)

            # Check pollution threshold
            if stats["pollution_ratio"] >= POLLUTION_THRESHOLD:
                logger.info(
                    f"Connection {connection_id} needs recycling: "
                    f"{stats['pollution_ratio']:.1%} pollution "
                    f"({stats['expired']}/{stats['total']} expired)"
                )
                # Create recycling task (stub implementation)
                asyncio.create_task(
                    self._initiate_recycling(connection_id),
                    name=f"recycle-{connection_id}",
                )

    async def _initiate_recycling(self, connection_id: str) -> None:
        """
        Initiate connection recycling.

        NOTE: This is a stub implementation. Full recycling workflow
        will be implemented in ENG-005 (Connection Recycler).
        """
        info = self._connections.get(connection_id)
        if not info:
            logger.warning(f"Cannot recycle {connection_id}: not found")
            return

        # Mark as draining
        info.is_draining = True
        info.connection.mark_draining()

        # Get active (non-expired) markets to migrate
        active_assets = list(self._registry.get_active_by_connection(connection_id))

        if not active_assets:
            # No active markets, just close
            logger.info(f"Connection {connection_id} has no active markets, closing")
            await self._remove_connection(connection_id)
            return

        # Create replacement connection
        new_connection_id = f"conn-{uuid.uuid4().hex[:8]}"
        new_connection = WebsocketConnection(
            connection_id=new_connection_id,
            asset_ids=active_assets,
            message_parser=self._message_parser,
            on_message=self._message_callback,
        )

        async with self._lock:
            self._connections[new_connection_id] = ConnectionInfo(
                connection=new_connection
            )

        # Start new connection
        await new_connection.start()

        # Wait for connection to stabilize
        await asyncio.sleep(2.0)

        # Reassign markets in registry
        await self._registry.reassign_connection(
            active_assets,
            connection_id,
            new_connection_id,
        )

        # Remove old connection
        await self._remove_connection(connection_id)

        logger.info(
            f"Recycled {connection_id} -> {new_connection_id} "
            f"with {len(active_assets)} active markets"
        )

    async def _remove_connection(self, connection_id: str) -> None:
        """Remove and cleanup a connection."""
        async with self._lock:
            info = self._connections.pop(connection_id, None)

        if info:
            await info.connection.stop()
            await self._registry.remove_connection(connection_id)
            logger.debug(f"Removed connection {connection_id}")

    def get_connection_stats(self) -> list[dict]:
        """Get stats for all connections."""
        result = []

        for cid, info in self._connections.items():
            conn_stats = info.connection.stats
            registry_stats = self._registry.connection_stats(cid)

            result.append(
                {
                    "connection_id": cid,
                    "status": info.connection.status.name,
                    "is_draining": info.is_draining,
                    "age_seconds": info.age,
                    "messages_received": conn_stats.messages_received,
                    "bytes_received": conn_stats.bytes_received,
                    "parse_errors": conn_stats.parse_errors,
                    "reconnect_count": conn_stats.reconnect_count,
                    "is_healthy": info.connection.is_healthy,
                    "total_markets": registry_stats["total"],
                    "subscribed_markets": registry_stats["subscribed"],
                    "expired_markets": registry_stats["expired"],
                    "pollution_ratio": registry_stats["pollution_ratio"],
                }
            )

        return result

    async def force_subscribe(self, asset_ids: list[str]) -> str:
        """
        Immediately create connection for given assets.

        Bypasses pending queue - used for priority subscriptions.
        Returns connection_id.
        """
        if len(asset_ids) > MAX_MARKETS_PER_CONNECTION:
            raise ValueError(
                f"Cannot subscribe to more than {MAX_MARKETS_PER_CONNECTION} markets, "
                f"got {len(asset_ids)}"
            )

        if not asset_ids:
            raise ValueError("Cannot force subscribe to empty asset list")

        connection_id = f"conn-{uuid.uuid4().hex[:8]}"
        connection = WebsocketConnection(
            connection_id=connection_id,
            asset_ids=asset_ids,
            message_parser=self._message_parser,
            on_message=self._message_callback,
        )

        async with self._lock:
            self._connections[connection_id] = ConnectionInfo(connection=connection)

        await connection.start()
        await self._registry.mark_subscribed(asset_ids, connection_id)

        logger.info(
            f"Force subscribed {len(asset_ids)} assets on connection {connection_id}"
        )

        return connection_id

    @property
    def recycler_stats(self) -> RecycleStats:
        """Get recycler statistics."""
        return self._recycler.stats
