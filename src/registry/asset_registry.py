import asyncio
import time
from typing import FrozenSet

from sortedcontainers import SortedDict

from src.registry.asset_entry import AssetEntry, AssetStatus


class AssetRegistry:
    """
    Central registry for all tracked assets.

    Provides efficient multi-index access:
        - By asset_id
        - By condition_id (market grouping)
        - By connection_id (for recycling)
        - By expiration_time (for lifecycle)
        - By status (for queries)
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Primary storage
        self._assets: dict[str, AssetEntry] = {}  # asset_id -> AssetEntry

        # Secondary indicies
        self._by_condition: dict[str, set[str]] = {}  # condition_id -> asset_id
        self._by_connection: dict[str, set[str]] = {}  # connection_id -> asset_id
        self._by_status: dict[AssetStatus, set[str]] = {
            status: set() for status in AssetStatus
        }

        # Expiration index: expiration_ts -> asset_ids
        self._by_expiration: SortedDict[int, set[str]] = SortedDict()

        self._pending_queue: list[str] = []

    # -------------------------------
    # Read-only operations (lock-free)
    # -------------------------------

    def get(self, asset_id: str) -> AssetEntry | None:
        return self._assets.get(asset_id)

    def get_by_condition(self, condition_id: str) -> FrozenSet[str]:
        return frozenset(self._by_condition.get(condition_id, set()))

    def get_by_connection(self, connection_id: str) -> FrozenSet[str]:
        return frozenset(self._by_connection.get(connection_id, set()))

    def get_active_by_connection(self, connection_id: str) -> FrozenSet[str]:
        """Get only active (SUBSCRIBED) assets for a connection, excluding expired."""
        asset_ids = self._by_connection.get(connection_id, set())
        active = {
            asset_id
            for asset_id in asset_ids
            if (entry := self._assets.get(asset_id))
            and entry.status == AssetStatus.SUBSCRIBED
        }
        return frozenset(active)

    def get_pending_count(self) -> int:
        """Get count of assets awaiting subscription."""
        return len(self._pending_queue)

    def get_expiring_before(self, timestamp: int) -> list[str]:
        """
        Get all asset_ids with expiration_ts before the given timestamp.

        Uses SortedDict.irange() for efficient range query.
        Returns list of asset_ids sorted by expiration time (earliest first).
        """
        result: list[str] = []
        for exp_ts in self._by_expiration.irange(
            maximum=timestamp, inclusive=(True, False)
        ):
            result.extend(self._by_expiration[exp_ts])
        return result

    def get_by_status(self, status: AssetStatus) -> FrozenSet[str]:
        """Get all asset_ids with the given status."""
        return frozenset(self._by_status.get(status, set()))

    def connection_stats(self, connection_id: str) -> dict[str, int | float]:
        """
        Get statistics for a connection.

        Returns dict with:
            - total: total assets assigned to connection
            - subscribed: count of SUBSCRIBED assets
            - expired: count of EXPIRED assets
            - pollution_ratio: expired / total (0.0 if total == 0)
        """
        asset_ids = self._by_connection.get(connection_id, set())
        total = len(asset_ids)

        if total == 0:
            return {
                "total": 0,
                "subscribed": 0,
                "expired": 0,
                "pollution_ratio": 0.0,
            }

        subscribed = 0
        expired = 0

        for asset_id in asset_ids:
            entry = self._assets.get(asset_id)
            if entry:
                if entry.status == AssetStatus.SUBSCRIBED:
                    subscribed += 1
                elif entry.status == AssetStatus.EXPIRED:
                    expired += 1

        return {
            "total": total,
            "subscribed": subscribed,
            "expired": expired,
            "pollution_ratio": expired / total if total > 0 else 0.0,
        }

    # ---------------------------
    # Mutation operations (require lock)
    # --------------------------

    async def add_asset(
        self, asset_id: str, condition_id: str, expiration_ts: int = 0
    ) -> bool:
        """
        Add new asset to registry

        Return False if asset already exists
        """
        if asset_id in self._assets:
            return False

        async with self._lock:
            if asset_id in self._assets:
                return False

            entry = AssetEntry(
                asset_id=asset_id,
                condition_id=condition_id,
                status=AssetStatus.PENDING,
                expiration_ts=expiration_ts,
            )

            self._assets[asset_id] = entry

            if condition_id not in self._by_condition:
                self._by_condition[condition_id] = set()

            self._by_condition[condition_id].add(asset_id)

            self._by_status[AssetStatus.PENDING].add(asset_id)

            if expiration_ts > 0:
                if expiration_ts not in self._by_expiration:
                    self._by_expiration[expiration_ts] = set()
                self._by_expiration[expiration_ts].add(asset_id)

            self._pending_queue.append(asset_id)

        return True

    async def mark_subscribed(self, asset_ids: list[str], connection_id: str) -> int:
        """
        Mark assets as subscribed to a connection

        Returns count of successfully updated assets
        """
        async with self._lock:
            count = 0
            now = time.monotonic()

            if connection_id not in self._by_connection:
                self._by_connection[connection_id] = set()

            for asset_id in asset_ids:
                entry = self._assets.get(asset_id)
                if not entry:
                    continue

                old_status = entry.status
                self._by_status[old_status].discard(asset_id)
                self._by_status[AssetStatus.SUBSCRIBED].add(asset_id)

                # Update entry
                entry.status = AssetStatus.SUBSCRIBED
                entry.connection_id = connection_id
                entry.subscribed_at = now

                # Update connection index
                self._by_connection[connection_id].add(asset_id)

                # Remove from pending queue
                if asset_id in self._pending_queue:
                    self._pending_queue.remove(asset_id)

                count += 1

            return count

    async def mark_expired(self, asset_ids: list[str]) -> int:
        """
        Mark markets as expired

        Doees not remove from connection - they're still subscribed, just no longer actively routing
        """
        async with self._lock:
            count = 0

            for asset_id in asset_ids:
                entry = self._assets.get(asset_id)
                if entry is None or entry.status == AssetStatus.EXPIRED:
                    continue

                old_status = entry.status
                self._by_status[old_status].discard(asset_id)
                self._by_status[AssetStatus.EXPIRED].add(asset_id)

                entry.status = AssetStatus.EXPIRED

                count += 1

            return count

    async def take_pending_batch(self, max_size=400) -> list[str]:
        """
        Take a batch of pending markets for subscription
        Removes from pending queue but doesn't change status yet
        """
        if max_size > 400:
            max_size = 400

        async with self._lock:
            batch = self._pending_queue[:max_size]
            self._pending_queue = self._pending_queue[max_size:]

            return batch

    async def reassign_connection(
        self,
        asset_ids: list[str],
        old_connection_id: str,
        new_connection_id: str,
    ) -> int:
        """
        Move assets from one connection to another

        Used during connection recycling
        """
        async with self._lock:
            count = 0

            if new_connection_id not in self._by_connection:
                self._by_connection[new_connection_id] = set()

            for asset_id in asset_ids:
                entry = self._assets.get(asset_id)
                if entry is None or entry.connection_id != old_connection_id:
                    continue

                # Update connectionn index
                self._by_connection[old_connection_id].discard(asset_id)
                self._by_connection[new_connection_id].add(asset_id)

                # Update entry
                entry.connection_id = new_connection_id
                count += 1

            return count

    async def remove_connection(self, connection_id: str) -> set[str]:
        """
        Remove connection from registry

        Returns set of asset_ids that were assigned.
        Assets are NOT removed - they become orphaned (connection_id = None)
        """
        async with self._lock:
            assets = self._by_connection.pop(connection_id, set())

            for asset_id in assets:
                entry = self._assets.get(asset_id)
                if entry and entry.connection_id == connection_id:
                    entry.connection_id = None

            return assets

    async def remove_asset(self, asset_id: str) -> bool:
        """
        Fully remove a market from the registry

        Used for cleanup of long-expired assets
        """
        async with self._lock:
            entry = self._assets.pop(asset_id, None)
            if entry is None:
                return False

            self._by_condition.get(entry.condition_id, set()).discard(asset_id)

            if entry.connection_id:
                self._by_connection.get(entry.connection_id, set()).discard(asset_id)

            self._by_status[entry.status].discard(asset_id)

            if entry.expiration_ts in self._by_expiration:
                self._by_expiration[entry.expiration_ts].discard(asset_id)
                if not self._by_expiration[entry.expiration_ts]:
                    del self._by_expiration[entry.expiration_ts]

            if asset_id in self._pending_queue:
                self._pending_queue.remove(asset_id)

            return True
