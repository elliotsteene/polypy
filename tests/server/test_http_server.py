"""Unit tests for HTTP server component."""

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.server import HTTPServer


class TestHTTPServerLifecycle:
    """Test HTTPServer lifecycle management."""

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Test basic start/stop lifecycle."""
        mock_app = MagicMock()
        server = HTTPServer(mock_app, port=8080)

        assert not server._running

        # Note: Cannot test actual start() without binding to port
        # This will be covered in integration tests

    @pytest.mark.asyncio
    async def test_start_already_running(self):
        """Test starting when already running logs warning."""
        mock_app = MagicMock()
        server = HTTPServer(mock_app)
        server._running = True

        # Should log warning and return early
        await server.start()  # This would fail if it tried to bind

    @pytest.mark.asyncio
    async def test_stop_not_running(self):
        """Test stopping when not running logs warning."""
        mock_app = MagicMock()
        server = HTTPServer(mock_app)

        # Should log warning and return early
        await server.stop()


class TestHealthEndpoint:
    """Test /health endpoint handler."""

    @pytest.mark.asyncio
    async def test_health_endpoint_healthy(self):
        """Test /health returns 200 when app is healthy."""
        # Mock PolyPy app
        mock_app = MagicMock()
        mock_app.is_healthy.return_value = True
        mock_app._registry = MagicMock()
        mock_app._pool = MagicMock()
        mock_app._pool.active_connection_count = 2
        mock_app._router = MagicMock()
        mock_app._workers = MagicMock()
        mock_app._workers.is_healthy.return_value = True
        mock_app._lifecycle = MagicMock()
        mock_app._lifecycle.is_running = True
        mock_app._recycler = MagicMock()
        mock_app._recycler.is_running = True

        server = HTTPServer(mock_app)

        # Create test client
        web_app = web.Application()
        web_app.router.add_get("/health", server._handle_health)

        async with TestClient(TestServer(web_app)) as client:
            resp = await client.get("/health")

            assert resp.status == 200
            data = await resp.json()

            assert data["healthy"] is True
            assert "timestamp" in data
            assert data["components"]["registry"] is True
            assert data["components"]["pool"] is True
            assert data["components"]["workers"] is True

    @pytest.mark.asyncio
    async def test_health_endpoint_unhealthy(self):
        """Test /health returns 503 when app is unhealthy."""
        mock_app = MagicMock()
        mock_app.is_healthy.return_value = False
        mock_app._registry = None
        mock_app._pool = None
        mock_app._router = None
        mock_app._workers = None
        mock_app._lifecycle = None
        mock_app._recycler = None

        server = HTTPServer(mock_app)

        web_app = web.Application()
        web_app.router.add_get("/health", server._handle_health)

        async with TestClient(TestServer(web_app)) as client:
            resp = await client.get("/health")

            assert resp.status == 503
            data = await resp.json()

            assert data["healthy"] is False
            assert data["components"]["registry"] is False
            assert data["components"]["pool"] is False

    @pytest.mark.asyncio
    async def test_health_endpoint_partial_startup(self):
        """Test /health during partial startup (some components None)."""
        mock_app = MagicMock()
        mock_app.is_healthy.return_value = False
        mock_app._registry = MagicMock()
        mock_app._pool = None  # Not started yet
        mock_app._router = MagicMock()
        mock_app._workers = None  # Not started yet
        mock_app._lifecycle = None
        mock_app._recycler = None

        server = HTTPServer(mock_app)

        web_app = web.Application()
        web_app.router.add_get("/health", server._handle_health)

        async with TestClient(TestServer(web_app)) as client:
            resp = await client.get("/health")

            assert resp.status == 503
            data = await resp.json()

            assert data["components"]["registry"] is True
            assert data["components"]["pool"] is False
            assert data["components"]["workers"] is False


class TestStatsEndpoint:
    """Test /stats endpoint handler."""

    @pytest.mark.asyncio
    async def test_stats_endpoint_returns_data(self):
        """Test /stats returns comprehensive stats."""
        mock_app = MagicMock()
        mock_app.get_stats.return_value = {
            "running": True,
            "registry": {"total_markets": 100},
            "pool": {"connection_count": 3},
            "router": {"messages_routed": 50000},
            "workers": {"alive_count": 4},
            "lifecycle": {"is_running": True},
            "recycler": {"recycles_initiated": 2},
        }

        server = HTTPServer(mock_app)

        web_app = web.Application()
        web_app.router.add_get("/stats", server._handle_stats)

        async with TestClient(TestServer(web_app)) as client:
            resp = await client.get("/stats")

            assert resp.status == 200
            data = await resp.json()

            assert data["running"] is True
            assert data["registry"]["total_markets"] == 100
            assert data["pool"]["connection_count"] == 3
            assert data["workers"]["alive_count"] == 4

    @pytest.mark.asyncio
    async def test_stats_endpoint_empty_stats(self):
        """Test /stats returns empty structure when not running."""
        mock_app = MagicMock()
        mock_app.get_stats.return_value = {
            "running": False,
            "registry": {},
            "pool": {},
            "router": {},
            "workers": {},
            "lifecycle": {},
            "recycler": {},
        }

        server = HTTPServer(mock_app)

        web_app = web.Application()
        web_app.router.add_get("/stats", server._handle_stats)

        async with TestClient(TestServer(web_app)) as client:
            resp = await client.get("/stats")

            assert resp.status == 200
            data = await resp.json()

            assert data["running"] is False
            assert data["registry"] == {}


class TestHTTPServerConfiguration:
    """Test HTTPServer configuration options."""

    def test_default_configuration(self):
        """Test default port and host."""
        mock_app = MagicMock()
        server = HTTPServer(mock_app)

        assert server._port == 8080
        assert server._host == "0.0.0.0"

    def test_custom_configuration(self):
        """Test custom port and host."""
        mock_app = MagicMock()
        server = HTTPServer(mock_app, port=9090, host="127.0.0.1")

        assert server._port == 9090
        assert server._host == "127.0.0.1"
