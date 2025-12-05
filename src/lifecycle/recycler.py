"""Connection recycler for zero-downtime connection migration."""

import asyncio
import time
from dataclasses import dataclass

import structlog

from src.connection.pool import ConnectionPool
from src.core.logging import Logger
from src.registry.asset_registry import AssetRegistry

logger: Logger = structlog.get_logger()

# Configuration constants
POLLUTION_THRESHOLD = 0.30  # 30% expired triggers recycling
AGE_THRESHOLD = 86400.0  # 24 hours max connection age (seconds)
HEALTH_CHECK_INTERVAL = 60.0  # Check health every 60 seconds
STABILIZATION_DELAY = 3.0  # Wait 3 seconds for new connection stability
MAX_CONCURRENT_RECYCLES = 2  # Limit concurrent operations


@dataclass(slots=True)
class RecycleStats:
    """Recycling performance metrics."""

    recycles_initiated: int = 0
    recycles_completed: int = 0
    recycles_failed: int = 0
    markets_migrated: int = 0
    total_downtime_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        """
        Percentage of successful recycles.

        Returns 1.0 (100%) when no recycles initiated.
        """
        if self.recycles_initiated > 0:
            return self.recycles_completed / self.recycles_initiated
        return 1.0

    @property
    def avg_downtime_ms(self) -> float:
        """
        Average downtime per completed recycle.

        Returns 0.0 if no recycles completed.
        """
        if self.recycles_completed > 0:
            return self.total_downtime_ms / self.recycles_completed
        return 0.0


class ConnectionRecycler:
    """
    Monitors connections and orchestrates zero-downtime recycling.

    Responsibilities:
    - Detect recycling triggers (pollution, age, health)
    - Coordinate seamless migration of markets to new connections
    - Track recycling statistics
    - Limit concurrent recycling operations
    """

    __slots__ = (
        "_registry",
        "_pool",
        "_running",
        "_monitor_task",
        "_active_recycles",
        "_active_recycle_tasks",
        "_stats",
        "_recycle_semaphore",
    )

    def __init__(
        self,
        registry: AssetRegistry,
        pool: ConnectionPool,
    ) -> None:
        """
        Initialize recycler.

        Args:
            registry: Asset registry for market coordination
            pool: Connection pool to monitor and recycle
        """
        self._registry = registry
        self._pool = pool
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._active_recycles: set[str] = set()
        self._active_recycle_tasks: set[asyncio.Task] = set()
        self._stats = RecycleStats()
        self._recycle_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RECYCLES)

    @property
    def stats(self) -> RecycleStats:
        """Read-only access to recycling statistics."""
        return self._stats

    @property
    def is_running(self) -> bool:
        """Whether the recycler is currently running."""
        return self._running

    def get_active_recycles(self) -> set[str]:
        """Get connection IDs currently being recycled."""
        return self._active_recycles.copy()

    async def _check_all_connections(self) -> None:
        """
        Check all connections for recycling triggers.

        Triggers:
        - Pollution ratio >= POLLUTION_THRESHOLD (30%)
        - Age >= AGE_THRESHOLD (24 hours)
        - Unhealthy status (is_healthy == False)

        Skips connections already being recycled or marked as draining.
        """
        connection_stats = self._pool.get_connection_stats()

        for stats in connection_stats:
            connection_id = stats["connection_id"]

            # Skip if already recycling
            if connection_id in self._active_recycles:
                logger.debug(f"Skipping {connection_id}: already recycling")
                continue

            # Skip if draining
            if stats.get("is_draining"):
                logger.debug(f"Skipping {connection_id}: already draining")
                continue

            # Check triggers
            should_recycle = False
            reason = ""

            pollution_ratio = stats.get("pollution_ratio", 0.0)
            age_seconds = stats.get("age_seconds", 0.0)
            is_healthy = stats.get("is_healthy", True)

            if pollution_ratio >= POLLUTION_THRESHOLD:
                should_recycle = True
                reason = f"pollution={pollution_ratio:.1%}"
            elif age_seconds >= AGE_THRESHOLD:
                should_recycle = True
                reason = f"age={age_seconds / 3600:.1f}h"
            elif not is_healthy:
                should_recycle = True
                reason = "unhealthy"

            if should_recycle:
                logger.info(f"Recycling trigger detected for {connection_id}: {reason}")
                task = asyncio.create_task(
                    self._recycle_connection(connection_id),
                    name=f"recycle-{connection_id}",
                )
                self._active_recycle_tasks.add(task)
                task.add_done_callback(self._active_recycle_tasks.discard)

    async def _recycle_connection(self, connection_id: str) -> bool:
        """
        Perform connection recycling with zero-downtime migration.

        Workflow:
        1. Acquire semaphore for concurrency control
        2. Get active markets from old connection
        3. Create new connection with those markets
        4. Wait STABILIZATION_DELAY for new connection
        5. Verify new connection is healthy
        6. Atomically update registry (reassign markets)
        7. Close old connection
        8. Update stats

        Args:
            connection_id: ID of connection to recycle

        Returns:
            True if successful, False otherwise
        """
        start_time = time.monotonic()

        try:
            async with self._recycle_semaphore:
                self._active_recycles.add(connection_id)
                self._stats.recycles_initiated += 1

                logger.info(f"Starting recycle for {connection_id}")

                # Step 1: Get active markets from old connection
                active_assets = list(
                    self._registry.get_active_by_connection(connection_id)
                )

                # Step 2: Handle empty connection
                if not active_assets:
                    logger.info(
                        f"Connection {connection_id} has no active markets, "
                        "removing without replacement"
                    )
                    await self._pool._remove_connection(connection_id)
                    self._stats.recycles_completed += 1
                    return True

                logger.info(
                    f"Recycling {connection_id} with {len(active_assets)} active markets"
                )

                # Step 3: Create new connection
                try:
                    new_connection_id = await self._pool.force_subscribe(active_assets)
                except Exception as e:
                    logger.error(
                        f"Failed to create replacement connection for {connection_id}: {e}",
                        exc_info=True,
                    )
                    self._stats.recycles_failed += 1
                    return False

                logger.info(
                    f"Created replacement connection {new_connection_id}, "
                    f"waiting {STABILIZATION_DELAY}s for stabilization"
                )

                # Step 4: Stabilization delay (both connections receiving messages)
                await asyncio.sleep(STABILIZATION_DELAY)

                # Step 5: Verify new connection health
                new_stats = None
                for stats in self._pool.get_connection_stats():
                    if stats["connection_id"] == new_connection_id:
                        new_stats = stats
                        break

                if not new_stats or not new_stats.get("is_healthy", False):
                    logger.error(
                        f"New connection {new_connection_id} is not healthy, "
                        f"aborting recycle of {connection_id}"
                    )
                    self._stats.recycles_failed += 1
                    return False

                logger.info(f"New connection {new_connection_id} is healthy")

                # Step 6: Atomic registry update
                try:
                    migrated_count = await self._registry.reassign_connection(
                        active_assets,
                        connection_id,
                        new_connection_id,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to reassign markets from {connection_id} "
                        f"to {new_connection_id}: {e}",
                        exc_info=True,
                    )
                    self._stats.recycles_failed += 1
                    return False

                logger.info(
                    f"Reassigned {migrated_count} markets from {connection_id} "
                    f"to {new_connection_id}"
                )

                # Step 7: Remove old connection
                try:
                    await self._pool._remove_connection(connection_id)
                except Exception as e:
                    logger.warning(
                        f"Error removing old connection {connection_id}: {e}",
                        exc_info=True,
                    )
                    # Continue - still count as success since markets are migrated

                # Step 8: Update stats
                duration_ms = (time.monotonic() - start_time) * 1000
                self._stats.recycles_completed += 1
                self._stats.markets_migrated += migrated_count
                self._stats.total_downtime_ms += duration_ms

                logger.info(
                    f"Successfully recycled {connection_id} -> {new_connection_id} "
                    f"in {duration_ms:.1f}ms with {migrated_count} markets"
                )

                return True

        except Exception as e:
            logger.error(
                f"Unexpected error recycling {connection_id}: {e}",
                exc_info=True,
            )
            self._stats.recycles_failed += 1
            return False
        finally:
            self._active_recycles.discard(connection_id)

    async def start(self) -> None:
        """
        Start the recycler monitoring loop.

        Begins checking connections every HEALTH_CHECK_INTERVAL seconds.
        """
        if self._running:
            logger.warning("ConnectionRecycler already running")
            return

        self._running = True

        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="connection-recycler-monitor",
        )

        logger.info(
            f"ConnectionRecycler started (check interval: {HEALTH_CHECK_INTERVAL}s, "
            f"pollution threshold: {POLLUTION_THRESHOLD:.0%}, "
            f"age threshold: {AGE_THRESHOLD / 3600:.0f}h)"
        )

    async def stop(self) -> None:
        """
        Stop the recycler and cleanup.

        Cancels monitoring task and waits for active recycles to complete.
        """
        if not self._running:
            return

        self._running = False

        # Cancel monitor task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Wait for active recycles to complete (with timeout)
        if self._active_recycle_tasks:
            logger.info(
                f"Waiting for {len(self._active_recycle_tasks)} active recycles to complete"
            )

            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_recycle_tasks, return_exceptions=True),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timed out waiting for {len(self._active_recycle_tasks)} recycle tasks"
                )
                # Cancel remaining tasks
                for task in self._active_recycle_tasks:
                    if not task.done():
                        task.cancel()

        logger.info(
            f"ConnectionRecycler stopped. Stats: "
            f"initiated={self._stats.recycles_initiated}, "
            f"completed={self._stats.recycles_completed}, "
            f"failed={self._stats.recycles_failed}, "
            f"success_rate={self._stats.success_rate:.1%}"
        )

    async def _monitor_loop(self) -> None:
        """
        Monitor connections for recycling needs.

        Runs every HEALTH_CHECK_INTERVAL seconds while running.
        """
        while self._running:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)

                if not self._running:
                    break

                await self._check_all_connections()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}", exc_info=True)
