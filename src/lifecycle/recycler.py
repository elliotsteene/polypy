"""Connection recycler for zero-downtime connection migration."""

import asyncio
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
                # TODO: Spawn recycling task (implemented in Phase 3)
