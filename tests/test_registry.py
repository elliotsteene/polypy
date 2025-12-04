"""Tests for asset registry and entry management."""

import pytest

from src.registry.asset_entry import AssetEntry, AssetStatus
from src.registry.asset_registry import AssetRegistry


class TestAssetEntry:
    """Test AssetEntry properties and status."""

    def test_is_active_when_subscribed(self) -> None:
        # Arrange
        entry = AssetEntry(
            asset_id="123",
            condition_id="condition_1",
            status=AssetStatus.SUBSCRIBED,
        )

        # Act & Assert
        assert entry.is_active is True

    def test_is_not_active_when_pending(self) -> None:
        # Arrange
        entry = AssetEntry(
            asset_id="123",
            condition_id="condition_1",
            status=AssetStatus.PENDING,
        )

        # Act & Assert
        assert entry.is_active is False

    def test_is_not_active_when_expired(self) -> None:
        # Arrange
        entry = AssetEntry(
            asset_id="123",
            condition_id="condition_1",
            status=AssetStatus.EXPIRED,
        )

        # Act & Assert
        assert entry.is_active is False


class TestAssetRegistry:
    """Test async asset registry operations."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    async def test_add_asset_creates_entry(self, registry: AssetRegistry) -> None:
        # Arrange
        asset_id = "123"
        condition_id = "condition_1"

        # Act
        added = await registry.add_asset(asset_id, condition_id)

        # Assert
        assert added is True
        entry = registry.get(asset_id)
        assert entry is not None
        assert entry.asset_id == asset_id
        assert entry.condition_id == condition_id
        assert entry.status == AssetStatus.PENDING

    async def test_add_asset_duplicate_returns_false(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        asset_id = "123"
        condition_id = "condition_1"
        await registry.add_asset(asset_id, condition_id)

        # Act
        added_again = await registry.add_asset(asset_id, condition_id)

        # Assert
        assert added_again is False

    async def test_mark_subscribed_updates_status(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        asset_id = "123"
        await registry.add_asset(asset_id, "condition_1")
        connection_id = "conn_1"

        # Act
        count = await registry.mark_subscribed([asset_id], connection_id)

        # Assert
        assert count == 1
        entry = registry.get(asset_id)
        assert entry is not None
        assert entry.status == AssetStatus.SUBSCRIBED
        assert entry.connection_id == connection_id

    async def test_mark_expired_updates_status(self, registry: AssetRegistry) -> None:
        # Arrange
        asset_id = "123"
        await registry.add_asset(asset_id, "condition_1")
        await registry.mark_subscribed([asset_id], "conn_1")

        # Act
        count = await registry.mark_expired([asset_id])

        # Assert
        assert count == 1
        entry = registry.get(asset_id)
        assert entry is not None
        assert entry.status == AssetStatus.EXPIRED

    async def test_get_by_condition_returns_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        condition_id = "condition_1"
        await registry.add_asset("123", condition_id)
        await registry.add_asset("456", condition_id)
        await registry.add_asset("789", "condition_2")  # Different condition

        # Act
        assets = registry.get_by_condition(condition_id)

        # Assert
        assert len(assets) == 2
        assert "123" in assets
        assert "456" in assets
        assert "789" not in assets

    async def test_get_by_connection_returns_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        await registry.add_asset("123", "condition_1")
        await registry.add_asset("456", "condition_1")
        await registry.mark_subscribed(["123", "456"], "conn_1")

        # Act
        assets = registry.get_by_connection("conn_1")

        # Assert
        assert len(assets) == 2
        assert "123" in assets
        assert "456" in assets

    async def test_take_pending_batch_respects_max_size(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        for i in range(10):
            await registry.add_asset(f"asset_{i}", "condition_1")

        # Act
        batch = await registry.take_pending_batch(max_size=5)

        # Assert
        assert len(batch) == 5
        # Next batch should have remaining 5
        batch2 = await registry.take_pending_batch(max_size=5)
        assert len(batch2) == 5

    async def test_reassign_connection_moves_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        await registry.add_asset("123", "condition_1")
        await registry.mark_subscribed(["123"], "conn_1")

        # Act
        count = await registry.reassign_connection(["123"], "conn_1", "conn_2")

        # Assert
        assert count == 1
        entry = registry.get("123")
        assert entry is not None
        assert entry.connection_id == "conn_2"
        # Check indices updated
        assert "123" in registry.get_by_connection("conn_2")
        assert "123" not in registry.get_by_connection("conn_1")

    async def test_remove_connection_orphans_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        await registry.add_asset("123", "condition_1")
        await registry.mark_subscribed(["123"], "conn_1")

        # Act
        assets = await registry.remove_connection("conn_1")

        # Assert
        assert "123" in assets
        entry = registry.get("123")
        assert entry is not None
        assert entry.connection_id is None  # Orphaned

    async def test_remove_asset_deletes_fully(self, registry: AssetRegistry) -> None:
        # Arrange
        await registry.add_asset("123", "condition_1")
        await registry.mark_subscribed(["123"], "conn_1")

        # Act
        removed = await registry.remove_asset("123")

        # Assert
        assert removed is True
        assert registry.get("123") is None
        assert "123" not in registry.get_by_condition("condition_1")
        assert "123" not in registry.get_by_connection("conn_1")
