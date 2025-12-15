"""Unit tests for WebSocket orderbook streaming handler."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.websocket_handler import (
    ClientSubscription,
    OrderbookStreamHandler,
)
from src.worker.protocol import OrderbookMetrics, OrderbookResponse


@dataclass
class MockPolyPy:
    """Mock PolyPy application for testing."""

    _workers: MagicMock
    _router: MagicMock


class TestClientSubscription:
    """Test ClientSubscription state management."""

    @pytest.mark.asyncio
    async def test_initial_state(self):
        """Test initial subscription state."""
        ws = MagicMock()
        client = ClientSubscription(ws)

        assert client.ws == ws
        assert client.asset_id is None
        assert client.stream_task is None

    @pytest.mark.asyncio
    async def test_cancel_stream_no_task(self):
        """Test cancelling when no stream task exists."""
        ws = MagicMock()
        client = ClientSubscription(ws)

        # Should not raise
        await client.cancel_stream()
        assert client.stream_task is None

    @pytest.mark.asyncio
    async def test_cancel_stream_with_task(self):
        """Test cancelling active stream task."""
        ws = MagicMock()
        client = ClientSubscription(ws)

        # Create a real task that will be cancelled
        async def dummy_coroutine():
            await asyncio.sleep(10)

        client.stream_task = asyncio.create_task(dummy_coroutine())

        await client.cancel_stream()

        assert client.stream_task is None

    @pytest.mark.asyncio
    async def test_close_connection(self):
        """Test closing connection cancels stream and closes WebSocket."""
        ws = MagicMock()
        ws.closed = False
        ws.close = AsyncMock()

        client = ClientSubscription(ws)

        # Create a real task that will be cancelled
        async def dummy_coroutine():
            await asyncio.sleep(10)

        client.stream_task = asyncio.create_task(dummy_coroutine())

        await client.close()

        ws.close.assert_called_once()


class TestOrderbookStreamHandler:
    """Test OrderbookStreamHandler."""

    def create_mock_app(self):
        """Create mock PolyPy application."""
        mock_workers = MagicMock()
        mock_router = MagicMock()
        mock_router.get_worker_for_asset.return_value = 0

        return MockPolyPy(_workers=mock_workers, _router=mock_router)

    @pytest.mark.asyncio
    async def test_handler_initialization(self):
        """Test handler initializes correctly."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        assert handler._app == mock_app
        assert len(handler._clients) == 0
        assert handler.get_client_count() == 0
        assert handler.get_subscribed_count() == 0

    @pytest.mark.asyncio
    async def test_format_orderbook_message(self):
        """Test orderbook response formatting."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        # Create mock response with metrics
        metrics = OrderbookMetrics(spread=0.01, mid_price=0.505, imbalance=0.2)
        response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-456",
            found=True,
            bids=[(0.50, 100.0), (0.49, 200.0)],
            asks=[(0.51, 150.0), (0.52, 250.0)],
            metrics=metrics,
            last_update_ts=1234567890,
        )

        message = handler._format_orderbook_message(response)

        assert message["type"] == "orderbook"
        assert message["asset_id"] == "asset-456"
        assert message["bids"] == [(0.50, 100.0), (0.49, 200.0)]
        assert message["asks"] == [(0.51, 150.0), (0.52, 250.0)]
        assert message["metrics"]["spread"] == 0.01
        assert message["metrics"]["mid_price"] == 0.505
        assert message["metrics"]["imbalance"] == 0.2
        assert message["last_update_ts"] == 1234567890

    @pytest.mark.asyncio
    async def test_format_orderbook_message_no_metrics(self):
        """Test orderbook formatting without metrics."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-456",
            found=True,
            bids=[(0.50, 100.0)],
            asks=[(0.51, 150.0)],
            metrics=None,
            last_update_ts=1234567890,
        )

        message = handler._format_orderbook_message(response)

        assert message["type"] == "orderbook"
        assert message["metrics"] is None

    @pytest.mark.asyncio
    async def test_handle_subscribe_message(self):
        """Test handling subscribe message."""
        mock_app = self.create_mock_app()

        # Mock workers returning orderbook
        mock_response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-123",
            found=True,
            bids=[(0.50, 100.0)],
            asks=[(0.51, 150.0)],
        )
        mock_app._workers.query_orderbook = AsyncMock(return_value=mock_response)

        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        ws.send_json = AsyncMock()
        client = ClientSubscription(ws)

        data = {"subscribe": "asset-123", "interval_ms": 1000}

        # Start subscription (stream will run until cancelled)
        await handler._handle_subscribe(client, data)

        # Give it a moment to start
        await asyncio.sleep(0.01)

        assert client.asset_id == "asset-123"
        assert client.stream_task is not None

        # Clean up
        await client.cancel_stream()

    @pytest.mark.asyncio
    async def test_handle_subscribe_cancels_existing_stream(self):
        """Test subscribing to new asset cancels existing stream."""
        mock_app = self.create_mock_app()

        # Mock workers returning orderbook
        mock_response = OrderbookResponse(
            request_id="test-123",
            asset_id="new-asset",
            found=True,
            bids=[(0.50, 100.0)],
            asks=[(0.51, 150.0)],
        )
        mock_app._workers.query_orderbook = AsyncMock(return_value=mock_response)

        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        ws.send_json = AsyncMock()
        client = ClientSubscription(ws)

        # Set up existing subscription with real task
        async def dummy_coroutine():
            await asyncio.sleep(10)

        old_task = asyncio.create_task(dummy_coroutine())
        client.stream_task = old_task
        client.asset_id = "old-asset"

        data = {"subscribe": "new-asset", "interval_ms": 500}

        await handler._handle_subscribe(client, data)

        # Give it a moment to start
        await asyncio.sleep(0.01)

        # Old task should be cancelled, new subscription started
        assert old_task.cancelled()
        assert client.asset_id == "new-asset"
        assert client.stream_task is not None
        assert client.stream_task != old_task

        # Clean up
        await client.cancel_stream()

    @pytest.mark.asyncio
    async def test_handle_unsubscribe_message(self):
        """Test handling unsubscribe message."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        client = ClientSubscription(ws)

        # Set up active subscription with real task
        async def dummy_coroutine():
            await asyncio.sleep(10)

        client.stream_task = asyncio.create_task(dummy_coroutine())
        client.asset_id = "asset-123"

        await handler._handle_unsubscribe(client)

        assert client.asset_id is None
        assert client.stream_task is None

    @pytest.mark.asyncio
    async def test_stream_orderbook_workers_not_available(self):
        """Test streaming fails when workers not available."""
        mock_app = self.create_mock_app()
        mock_app._workers = None  # Simulate workers not available

        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        ws.send_json = AsyncMock()

        client = ClientSubscription(ws)

        await handler._stream_orderbook(client, "asset-123", 0.5)

        # Should send error message
        ws.send_json.assert_called()
        call_args = ws.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "Workers not available" in call_args["error"]

    @pytest.mark.asyncio
    async def test_stream_orderbook_asset_not_found(self):
        """Test streaming fails when asset not found."""
        mock_app = self.create_mock_app()

        # Mock worker returning not found
        mock_response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-456",
            found=False,
            error="Asset not found in worker",
        )
        mock_app._workers.query_orderbook = AsyncMock(return_value=mock_response)

        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        ws.send_json = AsyncMock()

        client = ClientSubscription(ws)

        await handler._stream_orderbook(client, "asset-456", 0.5)

        # Should send error message
        ws.send_json.assert_called()
        call_args = ws.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "Asset not found" in call_args["error"]

    @pytest.mark.asyncio
    async def test_stream_orderbook_success(self):
        """Test successful orderbook streaming."""
        mock_app = self.create_mock_app()

        # Mock worker returning orderbook
        mock_response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-456",
            found=True,
            bids=[(0.50, 100.0)],
            asks=[(0.51, 150.0)],
            metrics=OrderbookMetrics(spread=0.01, mid_price=0.505, imbalance=0.0),
            last_update_ts=1234567890,
        )
        mock_app._workers.query_orderbook = AsyncMock(return_value=mock_response)

        handler = OrderbookStreamHandler(mock_app)

        ws = MagicMock()
        ws.send_json = AsyncMock()

        client = ClientSubscription(ws)

        # Run streaming once (cancel after first iteration)
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await handler._stream_orderbook(client, "asset-456", 0.5)

        # Should send orderbook message
        ws.send_json.assert_called()
        call_args = ws.send_json.call_args[0][0]
        assert call_args["type"] == "orderbook"
        assert call_args["asset_id"] == "asset-456"
        assert call_args["bids"] == [(0.50, 100.0)]
        assert call_args["asks"] == [(0.51, 150.0)]

    @pytest.mark.asyncio
    async def test_client_count_tracking(self):
        """Test client count tracking."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        ws1 = MagicMock()
        ws2 = MagicMock()

        client1 = ClientSubscription(ws1)
        client2 = ClientSubscription(ws2)

        handler._clients[ws1] = client1
        handler._clients[ws2] = client2

        assert handler.get_client_count() == 2

    @pytest.mark.asyncio
    async def test_subscribed_count_tracking(self):
        """Test subscribed client count tracking."""
        mock_app = self.create_mock_app()
        handler = OrderbookStreamHandler(mock_app)

        ws1 = MagicMock()
        ws2 = MagicMock()
        ws3 = MagicMock()

        client1 = ClientSubscription(ws1)
        client1.asset_id = "asset-1"

        client2 = ClientSubscription(ws2)
        client2.asset_id = "asset-2"

        client3 = ClientSubscription(ws3)
        # client3 has no subscription

        handler._clients[ws1] = client1
        handler._clients[ws2] = client2
        handler._clients[ws3] = client3

        assert handler.get_client_count() == 3
        assert handler.get_subscribed_count() == 2

    @pytest.mark.asyncio
    async def test_multiple_clients_independent_subscriptions(self):
        """Test multiple clients can have independent subscriptions."""
        mock_app = self.create_mock_app()

        # Mock workers returning orderbooks
        mock_response = OrderbookResponse(
            request_id="test-123",
            asset_id="asset-1",
            found=True,
            bids=[(0.50, 100.0)],
            asks=[(0.51, 150.0)],
        )
        mock_app._workers.query_orderbook = AsyncMock(return_value=mock_response)

        handler = OrderbookStreamHandler(mock_app)

        ws1 = MagicMock()
        ws1.send_json = AsyncMock()
        ws2 = MagicMock()
        ws2.send_json = AsyncMock()

        client1 = ClientSubscription(ws1)
        client2 = ClientSubscription(ws2)

        handler._clients[ws1] = client1
        handler._clients[ws2] = client2

        # Subscribe client1 to asset-1
        data1 = {"subscribe": "asset-1", "interval_ms": 500}
        await handler._handle_subscribe(client1, data1)

        # Subscribe client2 to asset-2
        data2 = {"subscribe": "asset-2", "interval_ms": 1000}
        await handler._handle_subscribe(client2, data2)

        # Give them a moment to start
        await asyncio.sleep(0.01)

        assert client1.asset_id == "asset-1"
        assert client2.asset_id == "asset-2"
        assert handler.get_subscribed_count() == 2

        # Clean up
        await client1.cancel_stream()
        await client2.cancel_stream()
