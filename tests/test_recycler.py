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
