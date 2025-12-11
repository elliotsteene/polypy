"""Market lifecycle controller."""

import asyncio
import time

import aiohttp
import structlog

from src.core.logging import Logger
from src.lifecycle.api import fetch_active_markets
from src.lifecycle.types import (
    CLEANUP_DELAY,
    DISCOVERY_INTERVAL,
    EXPIRATION_CHECK_INTERVAL,
    LifecycleCallback,
)
from src.registry.asset_entry import AssetStatus
from src.registry.asset_registry import AssetRegistry

logger: Logger = structlog.get_logger()


class LifecycleController:
    """
    Manages market lifecycle: discovery, expiration detection, and cleanup.

    Background tasks:
    - Discovery loop: Fetches new markets from Gamma API every 60s
    - Expiration loop: Checks for expired markets every 30s
    - Cleanup loop: Removes stale expired markets every hour
    """

    __slots__ = (
        "_registry",
        "_on_new_market",
        "_on_market_expired",
        "_session",
        "_known_conditions",
        "_discovery_task",
        "_expiration_task",
        "_cleanup_task",
        "_running",
    )

    def __init__(
        self,
        registry: AssetRegistry,
        on_new_market: LifecycleCallback | None = None,
        on_market_expired: LifecycleCallback | None = None,
    ) -> None:
        """
        Initialize lifecycle controller.

        Args:
            registry: Asset registry for market storage
            on_new_market: Optional callback for new market discovery
            on_market_expired: Optional callback for market expiration
        """
        self._registry = registry
        self._on_new_market = on_new_market
        self._on_market_expired = on_market_expired

        self._session: aiohttp.ClientSession | None = None
        self._known_conditions: set[str] = set()

        self._discovery_task: asyncio.Task | None = None
        self._expiration_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Whether the controller is currently running."""
        return self._running

    @property
    def known_market_count(self) -> int:
        """Number of known market condition IDs."""
        return len(self._known_conditions)

    async def start(self) -> None:
        """
        Start lifecycle management.

        Performs initial discovery, then starts background tasks.
        """
        if self._running:
            logger.warning("LifecycleController already running")
            return

        self._running = True

        # Create HTTP session
        self._session = aiohttp.ClientSession()

        # Initial discovery
        # await self._discover_markets()

        # Start background tasks
        self._discovery_task = asyncio.create_task(
            self._discovery_loop(),
            name="lifecycle-discovery",
        )
        self._expiration_task = asyncio.create_task(
            self._expiration_loop(),
            name="lifecycle-expiration",
        )
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="lifecycle-cleanup",
        )

        logger.info(
            f"LifecycleController started with {self.known_market_count} known markets"
        )

    async def stop(self) -> None:
        """Stop lifecycle management and cleanup resources."""
        if not self._running:
            return

        self._running = False

        # Cancel background tasks
        tasks = [self._discovery_task, self._expiration_task, self._cleanup_task]
        for task in tasks:
            if task:
                task.cancel()

        # Wait for tasks to finish
        for task in tasks:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None

        logger.info(
            f"LifecycleController stopped. "
            f"Final known markets: {self.known_market_count}"
        )

    async def add_market_manually(
        self,
        asset_id: str,
        condition_id: str,
        expiration_ts: int = 0,
    ) -> bool:
        """
        Add a market manually, bypassing discovery.

        Useful for testing or priority subscriptions.

        Args:
            asset_id: Token ID for the asset
            condition_id: Market condition ID
            expiration_ts: Optional expiration timestamp (unix ms)

        Returns:
            True if added, False if already exists
        """
        added = await self._registry.add_asset(asset_id, condition_id, expiration_ts)

        if added:
            self._known_conditions.add(condition_id)
            logger.info(
                f"Manually added market: asset={asset_id[:16]}... "
                f"condition={condition_id[:16]}..."
            )

            if self._on_new_market:
                try:
                    await self._on_new_market("new_market", asset_id)
                except Exception as e:
                    logger.error(f"on_new_market callback error: {e}")

        return added

    async def _discovery_loop(self) -> None:
        """Background task: Periodic market discovery."""
        while self._running:
            try:
                if len(self._known_conditions) > 0:
                    await asyncio.sleep(DISCOVERY_INTERVAL)

                if not self._running:
                    break

                await self._discover_markets()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Discovery loop error: {e}", exc_info=True)

    async def _discover_markets(self) -> None:
        """Fetch and register new markets from Gamma API."""
        if not self._session:
            logger.warning("No HTTP session available for discovery")
            return

        try:
            markets = await fetch_active_markets(self._session)
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return

        new_count = 0

        for market in markets:
            # Skip already known markets
            if market.condition_id in self._known_conditions:
                continue

            self._known_conditions.add(market.condition_id)

            # Register each token (Yes/No) as an asset
            for token in market.tokens:
                token_id = token.get("token_id", "")
                if not token_id:
                    continue

                added = await self._registry.add_asset(
                    asset_id=token_id,
                    condition_id=market.condition_id,
                    expiration_ts=market.end_timestamp,
                )

                if added:
                    new_count += 1

                    if self._on_new_market:
                        try:
                            await self._on_new_market("new_market", token_id)
                        except Exception as e:
                            logger.error(f"on_new_market callback error: {e}")

        if new_count > 0:
            logger.info(
                f"Discovered {new_count} new tokens from {len(markets)} markets"
            )

    async def _expiration_loop(self) -> None:
        """Background task: Periodic expiration checking."""
        while self._running:
            try:
                await asyncio.sleep(EXPIRATION_CHECK_INTERVAL)

                if not self._running:
                    break

                await self._check_expirations()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Expiration loop error: {e}", exc_info=True)

    async def _check_expirations(self) -> None:
        """Check for and mark expired markets."""
        current_time = int(time.time() * 1000)  # Unix milliseconds

        # Get assets expiring before now
        expiring_assets = self._registry.get_expiring_before(current_time)

        if not expiring_assets:
            return

        # Filter to only SUBSCRIBED assets (skip PENDING and already EXPIRED)
        subscribed = self._registry.get_by_status(AssetStatus.SUBSCRIBED)
        to_expire = [a for a in expiring_assets if a in subscribed]

        if not to_expire:
            return

        # Mark as expired
        count = await self._registry.mark_expired(to_expire)

        logger.info(f"Marked {count} assets as expired")

        # Invoke callbacks
        if self._on_market_expired:
            for asset_id in to_expire:
                try:
                    await self._on_market_expired("market_expired", asset_id)
                except Exception as e:
                    logger.error(f"on_market_expired callback error: {e}")

    async def _cleanup_loop(self) -> None:
        """Background task: Periodic cleanup of old expired markets."""
        while self._running:
            try:
                await asyncio.sleep(CLEANUP_DELAY)

                if not self._running:
                    break

                await self._cleanup_expired()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}", exc_info=True)

    async def _cleanup_expired(self) -> None:
        """Remove markets expired longer than grace period."""
        expired_assets = self._registry.get_by_status(AssetStatus.EXPIRED)

        if not expired_assets:
            return

        current_time = time.monotonic()
        removed_count = 0

        for asset_id in expired_assets:
            entry = self._registry.get(asset_id)
            if not entry:
                continue

            # Check age since subscription (or creation if never subscribed)
            age = current_time - (entry.subscribed_at or entry.created_at)

            if age > CLEANUP_DELAY:
                await self._registry.remove_asset(asset_id)
                removed_count += 1

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} expired assets")
