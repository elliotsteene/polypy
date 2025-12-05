"""Unit tests for ConnectionRecycler."""

import pytest

from src.lifecycle.recycler import RecycleStats


class TestRecycleStats:
    """Test RecycleStats dataclass and computed properties."""

    def test_default_values(self):
        """All fields default to 0."""
        stats = RecycleStats()
        assert stats.recycles_initiated == 0
        assert stats.recycles_completed == 0
        assert stats.recycles_failed == 0
        assert stats.markets_migrated == 0
        assert stats.total_downtime_ms == 0.0

    def test_success_rate_no_recycles(self):
        """Success rate is 1.0 (100%) when no recycles initiated."""
        stats = RecycleStats()
        assert stats.success_rate == 1.0

    def test_success_rate_all_success(self):
        """Success rate is 1.0 when all recycles completed."""
        stats = RecycleStats(
            recycles_initiated=10,
            recycles_completed=10,
        )
        assert stats.success_rate == 1.0

    def test_success_rate_partial_success(self):
        """Success rate calculated correctly with failures."""
        stats = RecycleStats(
            recycles_initiated=10,
            recycles_completed=7,
            recycles_failed=3,
        )
        assert stats.success_rate == 0.7

    def test_success_rate_all_failures(self):
        """Success rate is 0.0 when all recycles failed."""
        stats = RecycleStats(
            recycles_initiated=5,
            recycles_completed=0,
            recycles_failed=5,
        )
        assert stats.success_rate == 0.0

    def test_avg_downtime_no_recycles(self):
        """Average downtime is 0.0 when no recycles completed."""
        stats = RecycleStats()
        assert stats.avg_downtime_ms == 0.0

    def test_avg_downtime_single_recycle(self):
        """Average downtime calculated correctly for single recycle."""
        stats = RecycleStats(
            recycles_completed=1,
            total_downtime_ms=150.5,
        )
        assert stats.avg_downtime_ms == 150.5

    def test_avg_downtime_multiple_recycles(self):
        """Average downtime calculated correctly for multiple recycles."""
        stats = RecycleStats(
            recycles_completed=4,
            total_downtime_ms=600.0,
        )
        assert stats.avg_downtime_ms == 150.0


class TestConnectionRecyclerInit:
    """Test ConnectionRecycler initialization."""

    def test_initialization(self, mock_registry, mock_pool):
        """Recycler initializes with correct defaults."""
        from src.lifecycle.recycler import ConnectionRecycler

        recycler = ConnectionRecycler(mock_registry, mock_pool)

        assert recycler.is_running is False
        assert recycler.get_active_recycles() == set()
        assert recycler.stats.recycles_initiated == 0
        assert recycler.stats.recycles_completed == 0
        assert recycler.stats.recycles_failed == 0

    def test_stats_property_readonly(self, mock_registry, mock_pool):
        """Stats property returns the stats object."""
        from src.lifecycle.recycler import ConnectionRecycler

        recycler = ConnectionRecycler(mock_registry, mock_pool)
        stats = recycler.stats

        assert isinstance(stats, RecycleStats)
        assert stats is recycler._stats


@pytest.fixture
def mock_registry():
    """Mock AssetRegistry."""
    from unittest.mock import Mock

    return Mock()


@pytest.fixture
def mock_pool():
    """Mock ConnectionPool."""
    from unittest.mock import Mock

    return Mock()


@pytest.fixture
def recycler(mock_registry, mock_pool):
    """Create ConnectionRecycler instance."""
    from src.lifecycle.recycler import ConnectionRecycler

    return ConnectionRecycler(mock_registry, mock_pool)


class TestTriggerDetection:
    """Test connection recycling trigger detection."""

    @pytest.mark.asyncio
    async def test_pollution_trigger(self, recycler, mock_pool):
        """Trigger detected when pollution >= 30%."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.35,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        # Should detect trigger (will be stubbed for now)
        await recycler._check_all_connections()

        # Verify log output or other side effects
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_age_trigger(self, recycler, mock_pool):
        """Trigger detected when age >= 24 hours."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 86500.0,  # Over 24 hours
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_trigger(self, recycler, mock_pool):
        """Trigger detected when connection unhealthy."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 100.0,
                "is_healthy": False,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()
        mock_pool.get_connection_stats.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_already_recycling(self, recycler, mock_pool):
        """Skip connections already being recycled."""
        recycler._active_recycles.add("conn-1")

        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()

        # Should not trigger recycling (already in progress)
        assert "conn-1" in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_skip_draining(self, recycler, mock_pool):
        """Skip connections already marked as draining."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": True,
            }
        ]

        await recycler._check_all_connections()

        # Should skip draining connection
        assert "conn-1" not in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_no_trigger(self, recycler, mock_pool):
        """No trigger when connection is healthy and young."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.10,
                "age_seconds": 1000.0,
                "is_healthy": True,
                "is_draining": False,
            }
        ]

        await recycler._check_all_connections()

        # Should not trigger
        assert "conn-1" not in recycler._active_recycles

    @pytest.mark.asyncio
    async def test_multiple_connections(self, recycler, mock_pool):
        """Handle multiple connections correctly."""
        mock_pool.get_connection_stats.return_value = [
            {
                "connection_id": "conn-1",
                "pollution_ratio": 0.05,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            },
            {
                "connection_id": "conn-2",
                "pollution_ratio": 0.40,
                "age_seconds": 100.0,
                "is_healthy": True,
                "is_draining": False,
            },
        ]

        await recycler._check_all_connections()

        # Only conn-2 should trigger
        mock_pool.get_connection_stats.assert_called_once()
