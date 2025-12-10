"""Integration tests for LifecycleController."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.lifecycle.controller import LifecycleController
from src.lifecycle.types import MarketInfo
from src.registry.asset_entry import AssetStatus
from src.registry.asset_registry import AssetRegistry


def create_mock_market(
    condition_id: str,
    token_ids: list[str],
    end_timestamp: int,
) -> MarketInfo:
    """Helper to create mock MarketInfo."""
    return MarketInfo(
        condition_id=condition_id,
        question=f"Test market {condition_id}?",
        outcomes=["Yes", "No"],
        tokens=[{"token_id": tid, "outcome": "Yes"} for tid in token_ids],
        end_date_iso="2024-12-31T23:59:59Z",
        end_timestamp=end_timestamp,
        active=True,
        closed=False,
    )


class TestLifecycleIntegration:
    """Integration tests for full lifecycle flow."""

    @pytest.fixture
    def registry(self) -> AssetRegistry:
        return AssetRegistry()

    @pytest.mark.asyncio
    async def test_full_lifecycle_discovery_to_cleanup(
        self, registry: AssetRegistry
    ) -> None:
        """Test complete lifecycle: discovery -> subscription -> expiration -> cleanup."""
        # Arrange
        events_captured: list[tuple[str, str]] = []

        async def capture_event(event: str, asset_id: str) -> None:
            events_captured.append((event, asset_id))

        controller = LifecycleController(
            registry,
            on_new_market=capture_event,
            on_market_expired=capture_event,
        )
        # Need to activate the sleep within task so that it yields back to the test
        controller._known_conditions.add("condition-id")

        # Create mock market that expires soon
        now_ms = int(time.time() * 1000)
        mock_market = create_mock_market(
            condition_id="test_condition",
            token_ids=["token_yes", "token_no"],
            end_timestamp=now_ms + 500,  # Expires in 500ms
        )

        # Mock the API to return our market
        mock_fetch = AsyncMock(return_value=[mock_market])

        with patch("src.lifecycle.controller.fetch_active_markets", mock_fetch):
            with patch("src.lifecycle.controller.DISCOVERY_INTERVAL", 0.1):
                with patch("src.lifecycle.controller.EXPIRATION_CHECK_INTERVAL", 0.1):
                    # Act - start controller
                    await controller.start()

                    # Wait for discovery
                    await asyncio.sleep(0.2)

                    # Verify discovery happened
                    assert registry.get("token_yes") is not None
                    assert registry.get("token_no") is not None
                    assert ("new_market", "token_yes") in events_captured
                    assert ("new_market", "token_no") in events_captured

                    # Simulate subscription (normally done by ConnectionPool)
                    await registry.mark_subscribed(["token_yes", "token_no"], "conn1")

                    # Wait for expiration
                    await asyncio.sleep(0.6)

                    # Verify expiration detected
                    entry = registry.get("token_yes")
                    assert entry is not None
                    assert entry.status == AssetStatus.EXPIRED

                    # Cleanup
                    await controller.stop()

    @pytest.mark.asyncio
    async def test_controller_handles_api_errors_gracefully(
        self, registry: AssetRegistry
    ) -> None:
        """Test that controller continues running after API errors."""
        # Arrange
        controller = LifecycleController(registry)
        # Need to activate the sleep within task so that it yields back to the test
        controller._known_conditions.add("condition-id")

        # Mock API to fail
        import aiohttp

        mock_fetch = AsyncMock(side_effect=aiohttp.ClientError("Network error"))

        with patch("src.lifecycle.controller.fetch_active_markets", mock_fetch):
            with patch("src.lifecycle.controller.DISCOVERY_INTERVAL", 0.1):
                # Act
                await controller.start()
                await asyncio.sleep(0.3)  # Let it try discovery a few times

                # Assert - controller should still be running
                assert controller._running is True

                # Cleanup
                await controller.stop()

    @pytest.mark.asyncio
    async def test_multiple_discovery_cycles_avoid_duplicates(
        self, registry: AssetRegistry
    ) -> None:
        """Test that repeated discoveries don't create duplicate assets."""
        # Arrange
        controller = LifecycleController(registry)

        now_ms = int(time.time() * 1000)
        mock_market = create_mock_market(
            condition_id="same_condition",
            token_ids=["token1"],
            end_timestamp=now_ms + 60000,
        )

        call_count = 0

        async def counting_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return [mock_market]

        with patch("src.lifecycle.controller.fetch_active_markets", counting_fetch):
            with patch("src.lifecycle.controller.DISCOVERY_INTERVAL", 0.1):
                # Act
                await controller.start()
                await asyncio.sleep(0.35)  # Allow 3 discovery cycles

                # Assert
                assert call_count >= 3  # Multiple API calls
                assert controller.known_market_count == 1  # But only one market tracked

                # Verify only one asset in registry
                entry = registry.get("token1")
                assert entry is not None

                # Cleanup
                await controller.stop()

    @pytest.mark.asyncio
    async def test_callback_errors_dont_stop_processing(
        self, registry: AssetRegistry
    ) -> None:
        """Test that callback errors don't crash the controller."""

        # Arrange
        async def failing_callback(event: str, asset_id: str) -> None:
            raise ValueError("Callback error!")

        controller = LifecycleController(
            registry,
            on_new_market=failing_callback,
        )

        now_ms = int(time.time() * 1000)
        mock_market = create_mock_market(
            condition_id="test",
            token_ids=["token1", "token2"],
            end_timestamp=now_ms + 60000,
        )

        mock_fetch = AsyncMock(return_value=[mock_market])

        with patch("src.lifecycle.controller.fetch_active_markets", mock_fetch):
            # Act
            await controller.start()
            await asyncio.sleep(0.1)

            # Assert - both tokens should be registered despite callback errors
            assert registry.get("token1") is not None
            assert registry.get("token2") is not None

            # Cleanup
            await controller.stop()
