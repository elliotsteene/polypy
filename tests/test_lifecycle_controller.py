"""Tests for LifecycleController."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.lifecycle.controller import LifecycleController
from src.lifecycle.types import CLEANUP_DELAY
from src.registry.asset_entry import AssetStatus
from src.registry.asset_registry import AssetRegistry


class TestLifecycleControllerInitialization:
    """Tests for controller initialization."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    def test_controller_initialization(self, registry: AssetRegistry) -> None:
        # Act
        controller = LifecycleController(registry)

        # Assert
        assert controller._registry is registry
        assert controller._on_new_market is None
        assert controller._on_market_expired is None
        assert controller._running is False
        assert controller.known_market_count == 0

    def test_controller_initialization_with_callbacks(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        async def on_new(event: str, asset_id: str) -> None:
            pass

        async def on_expired(event: str, asset_id: str) -> None:
            pass

        # Act
        controller = LifecycleController(
            registry,
            on_new_market=on_new,
            on_market_expired=on_expired,
        )

        # Assert
        assert controller._on_new_market is on_new
        assert controller._on_market_expired is on_expired


class TestLifecycleControllerLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self, registry: AssetRegistry) -> None:
        # Arrange
        controller = LifecycleController(registry)

        with patch.object(
            LifecycleController, "_discover_markets", new_callable=AsyncMock
        ):
            # Act
            await controller.start()

            # Assert
            assert controller._running is True
            assert controller._session is not None
            assert controller._discovery_task is not None
            assert controller._expiration_task is not None
            assert controller._cleanup_task is not None

            # Cleanup
            await controller.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self, registry: AssetRegistry) -> None:
        # Arrange
        controller = LifecycleController(registry)

        with patch.object(
            LifecycleController, "_discover_markets", new_callable=AsyncMock
        ):
            await controller.start()

            # Act
            await controller.stop()

            # Assert
            assert controller._running is False
            assert controller._session is None

    @pytest.mark.asyncio
    async def test_multiple_start_calls_are_idempotent(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        with patch.object(
            LifecycleController, "_discover_markets", new_callable=AsyncMock
        ):
            # Act
            await controller.start()
            first_task = controller._discovery_task
            await controller.start()
            second_task = controller._discovery_task

            # Assert
            assert first_task is second_task

            # Cleanup
            await controller.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, registry: AssetRegistry) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Act & Assert (should not raise)
        await controller.stop()
        assert controller._running is False


class TestManualMarketAddition:
    """Tests for add_market_manually."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_add_market_manually_adds_to_registry(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Act
        added = await controller.add_market_manually(
            asset_id="token123",
            condition_id="condition456",
            expiration_ts=int(time.time() * 1000) + 60000,
        )

        # Assert
        assert added is True
        assert registry.get("token123") is not None
        assert "condition456" in controller._known_conditions

    @pytest.mark.asyncio
    async def test_add_market_manually_returns_false_for_duplicate(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)
        await controller.add_market_manually("token123", "condition456")

        # Act
        added_again = await controller.add_market_manually("token123", "condition456")

        # Assert
        assert added_again is False

    @pytest.mark.asyncio
    async def test_add_market_manually_invokes_callback(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        callback = AsyncMock()
        controller = LifecycleController(registry, on_new_market=callback)

        # Act
        await controller.add_market_manually("token123", "condition456")

        # Assert
        callback.assert_called_once_with("new_market", "token123")


class TestExpirationChecking:
    """Tests for expiration detection."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_check_expirations_marks_expired_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Add asset that expired 1 second ago
        past_time = int(time.time() * 1000) - 1000
        await registry.add_asset("expired_asset", "condition1", past_time)
        await registry.mark_subscribed(["expired_asset"], "conn1")

        # Act
        await controller._check_expirations()

        # Assert
        entry = registry.get("expired_asset")
        assert entry is not None
        assert entry.status == AssetStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_check_expirations_skips_pending_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Add asset that expired but is still PENDING
        past_time = int(time.time() * 1000) - 1000
        await registry.add_asset("pending_asset", "condition1", past_time)
        # Don't mark as subscribed

        # Act
        await controller._check_expirations()

        # Assert
        entry = registry.get("pending_asset")
        assert entry is not None
        assert entry.status == AssetStatus.PENDING  # Not changed

    @pytest.mark.asyncio
    async def test_check_expirations_invokes_callback(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        callback = AsyncMock()
        controller = LifecycleController(registry, on_market_expired=callback)

        past_time = int(time.time() * 1000) - 1000
        await registry.add_asset("expired_asset", "condition1", past_time)
        await registry.mark_subscribed(["expired_asset"], "conn1")

        # Act
        await controller._check_expirations()

        # Assert
        callback.assert_called_once_with("market_expired", "expired_asset")


class TestCleanup:
    """Tests for expired asset cleanup."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_expired_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Add and expire an asset
        await registry.add_asset("old_expired", "condition1", 0)
        await registry.mark_subscribed(["old_expired"], "conn1")
        await registry.mark_expired(["old_expired"])

        # Manually set subscribed_at to be old enough
        entry = registry.get("old_expired")
        entry.subscribed_at = time.monotonic() - CLEANUP_DELAY - 100

        # Act
        await controller._cleanup_expired()

        # Assert
        assert registry.get("old_expired") is None

    @pytest.mark.asyncio
    async def test_cleanup_keeps_recently_expired_assets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)

        # Add and expire an asset recently
        await registry.add_asset("recent_expired", "condition1", 0)
        await registry.mark_subscribed(["recent_expired"], "conn1")
        await registry.mark_expired(["recent_expired"])
        # subscribed_at is set to now, so it's recent

        # Act
        await controller._cleanup_expired()

        # Assert
        assert registry.get("recent_expired") is not None


class TestDiscoveryLoop:
    """Tests for discovery loop behavior."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_discovery_loop_calls_discover_markets(
        self, registry: AssetRegistry
    ) -> None:
        # Arrange
        controller = LifecycleController(registry)
        discover_mock = AsyncMock()

        with patch.object(LifecycleController, "_discover_markets", discover_mock):
            with patch("src.lifecycle.controller.DISCOVERY_INTERVAL", 0.1):
                # Start and let run briefly
                controller._running = True
                controller._session = MagicMock()

                # Run discovery loop for a short time
                task = asyncio.create_task(controller._discovery_loop())
                await asyncio.sleep(0.25)
                controller._running = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Assert - should have been called at least once
        assert discover_mock.call_count >= 1
