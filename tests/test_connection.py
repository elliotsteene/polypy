"""Tests for connection statistics and WebSocket handling."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connection.stats import ConnectionStats


class TestConnectionStats:
    """Test connection statistics calculations."""

    def test_uptime_calculation(self) -> None:
        # Arrange
        stats = ConnectionStats()
        initial_time = stats.created_at

        # Act - simulate time passing
        with patch("time.monotonic", return_value=initial_time + 10.0):
            uptime = stats.uptime

        # Assert
        assert uptime == 10.0

    def test_message_rate_with_messages(self) -> None:
        # Arrange
        stats = ConnectionStats()
        stats.messages_received = 100
        initial_time = stats.created_at

        # Act
        with patch("time.monotonic", return_value=initial_time + 10.0):
            rate = stats.message_rate

        # Assert
        assert rate == 10.0  # 100 messages / 10 seconds

    def test_message_rate_zero_when_no_time_elapsed(self) -> None:
        # Arrange
        stats = ConnectionStats()
        stats.messages_received = 100

        # Act - immediately after creation
        with patch("time.monotonic", return_value=stats.created_at):
            rate = stats.message_rate

        # Assert
        assert rate == 0.0

    def test_message_rate_zero_when_no_messages(self) -> None:
        # Arrange
        stats = ConnectionStats()
        initial_time = stats.created_at

        # Act
        with patch("time.monotonic", return_value=initial_time + 10.0):
            rate = stats.message_rate

        # Assert
        assert rate == 0.0


class TestWebsocketConnection:
    """Test WebSocket connection with mocked I/O."""

    @pytest.fixture
    def mock_websocket(self):
        """Mock websocket connection."""
        ws = AsyncMock()
        ws.recv = AsyncMock()
        ws.send = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.fixture
    def mock_callback(self):
        """Mock message callback."""
        return AsyncMock()

    @pytest.fixture
    def mock_parser(self):
        """Mock message parser."""
        parser = MagicMock()
        parser.parse_messages = MagicMock(return_value=iter([]))
        return parser

    async def test_websocket_initialization(self, mock_parser, mock_callback) -> None:
        # Arrange
        from src.connection.websocket import WebsocketConnection

        # Act
        conn = WebsocketConnection(
            connection_id="test_conn",
            asset_ids=["123", "456"],
            message_parser=mock_parser,
            on_message=mock_callback,
        )

        # Assert
        assert conn.connection_id == "test_conn"
        assert conn.asset_ids == ["123", "456"]
        assert conn.stats.messages_received == 0
        assert conn.stats.reconnect_count == 0

    async def test_websocket_initialization_rejects_too_many_assets(
        self, mock_parser, mock_callback
    ) -> None:
        # Arrange
        from src.connection.websocket import WebsocketConnection

        too_many_assets = [str(i) for i in range(501)]

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot subscribe to more than 500"):
            WebsocketConnection(
                connection_id="test_conn",
                asset_ids=too_many_assets,
                message_parser=mock_parser,
                on_message=mock_callback,
            )

    async def test_track_stats_updates_counters(
        self, mock_parser, mock_callback
    ) -> None:
        # Arrange
        from src.connection.websocket import WebsocketConnection

        conn = WebsocketConnection(
            connection_id="test_conn",
            asset_ids=["123"],
            message_parser=mock_parser,
            on_message=mock_callback,
        )

        test_data = b'{"event_type":"book","market":"0xabc"}'

        # Act
        conn._track_stats(test_data)

        # Assert
        assert conn.stats.messages_received == 1
        assert conn.stats.bytes_received == len(test_data)
        assert conn.stats.last_message_ts > 0

    async def test_is_healthy_when_recently_received_messages(
        self, mock_parser, mock_callback
    ) -> None:
        # Arrange
        from src.connection.types import ConnectionStatus
        from src.connection.websocket import WebsocketConnection

        conn = WebsocketConnection(
            connection_id="test_conn",
            asset_ids=["123"],
            message_parser=mock_parser,
            on_message=mock_callback,
        )
        conn._status = ConnectionStatus.CONNECTED
        conn._stats.last_message_ts = time.monotonic()

        # Act
        healthy = conn.is_healthy

        # Assert
        assert healthy is True

    async def test_is_not_healthy_when_silent_too_long(
        self, mock_parser, mock_callback
    ) -> None:
        # Arrange
        from src.connection.types import ConnectionStatus
        from src.connection.websocket import WebsocketConnection

        conn = WebsocketConnection(
            connection_id="test_conn",
            asset_ids=["123"],
            message_parser=mock_parser,
            on_message=mock_callback,
        )
        conn._status = ConnectionStatus.CONNECTED
        conn._stats.last_message_ts = time.monotonic() - 61.0  # 61 seconds ago

        # Act
        healthy = conn.is_healthy

        # Assert
        assert healthy is False

    async def test_is_not_healthy_when_disconnected(
        self, mock_parser, mock_callback
    ) -> None:
        # Arrange
        from src.connection.types import ConnectionStatus
        from src.connection.websocket import WebsocketConnection

        conn = WebsocketConnection(
            connection_id="test_conn",
            asset_ids=["123"],
            message_parser=mock_parser,
            on_message=mock_callback,
        )
        conn._status = ConnectionStatus.DISCONNECTED

        # Act
        healthy = conn.is_healthy

        # Assert
        assert healthy is False
