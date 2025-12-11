"""Tests for Prometheus metrics collection."""

import pytest
from prometheus_client.parser import text_string_to_metric_families

from src.metrics.prometheus import MetricsCollector


@pytest.fixture
def mock_stats() -> dict:
    """Mock stats dictionary matching PolyPy.get_stats() structure."""
    return {
        "running": True,
        "registry": {
            "total_markets": 150,
            "pending": 10,
            "subscribed": 120,
            "expired": 20,
        },
        "pool": {
            "connection_count": 5,
            "active_connections": 4,
            "total_capacity": 2500,
            "stats": [],
        },
        "router": {
            "messages_routed": 10000,
            "messages_dropped": 50,
            "batches_sent": 500,
            "queue_full_events": 5,
            "routing_errors": 2,
            "avg_latency_ms": 1.5,
            "queue_depths": {"async_queue": 10, "worker_0": 5},
        },
        "workers": {
            "alive_count": 2,
            "is_healthy": True,
            "worker_stats": {},
        },
        "lifecycle": {
            "is_running": True,
            "known_market_count": 100,
        },
        "recycler": {
            "recycles_initiated": 10,
            "recycles_completed": 8,
            "recycles_failed": 2,
            "success_rate": 0.8,
            "markets_migrated": 500,
            "avg_downtime_ms": 150.0,
            "active_recycles": [],
        },
    }


class TestMetricsCollector:
    """Test MetricsCollector class."""

    def test_collect_metrics_returns_bytes(self, mock_stats):
        """Test that collect_metrics returns bytes."""

        # Create mock app
        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_metrics_are_valid_prometheus_format(self, mock_stats):
        """Test that output is valid Prometheus text format."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()

        # Parse using prometheus_client parser
        metrics_text = result.decode("utf-8")
        families = list(text_string_to_metric_families(metrics_text))

        # Should have multiple metric families
        assert len(families) > 0

        # Check for expected metric names
        # Note: prometheus_client parser strips _total suffix from counters
        metric_names = {family.name for family in families}
        assert "polypy_application_running" in metric_names
        assert "polypy_registry_markets_total" in metric_names
        assert "polypy_router_messages_routed" in metric_names

    def test_application_running_metric(self, mock_stats):
        """Test application running metric."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for running=1
        assert "polypy_application_running 1.0" in metrics_text

    def test_registry_metrics(self, mock_stats):
        """Test registry metrics with status labels."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for market counts by status
        assert 'polypy_registry_markets_total{status="total"} 150.0' in metrics_text
        assert 'polypy_registry_markets_total{status="pending"} 10.0' in metrics_text
        assert (
            'polypy_registry_markets_total{status="subscribed"} 120.0' in metrics_text
        )
        assert 'polypy_registry_markets_total{status="expired"} 20.0' in metrics_text

    def test_router_counter_metrics(self, mock_stats):
        """Test router counter metrics."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for counters
        assert "polypy_router_messages_routed_total 10000.0" in metrics_text
        assert "polypy_router_messages_dropped_total 50.0" in metrics_text
        assert "polypy_router_batches_sent_total 500.0" in metrics_text

    def test_recycler_metrics_with_labels(self, mock_stats):
        """Test recycler metrics with result labels."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for labeled counters
        assert 'polypy_recycler_recycles_total{result="completed"} 8.0' in metrics_text
        assert 'polypy_recycler_recycles_total{result="failed"} 2.0' in metrics_text
        assert "polypy_recycler_success_rate 0.8" in metrics_text

    def test_empty_stats_handling(self):
        """Test that collector handles empty stats gracefully."""

        class MockApp:
            def get_stats(self):
                return {
                    "running": False,
                    "registry": {},
                    "pool": {},
                    "router": {},
                    "workers": {},
                    "lifecycle": {},
                    "recycler": {},
                }

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()

        # Should still return valid Prometheus format
        assert isinstance(result, bytes)
        metrics_text = result.decode("utf-8")
        families = list(text_string_to_metric_families(metrics_text))

        # Should at least have application_running metric
        metric_names = {family.name for family in families}
        assert "polypy_application_running" in metric_names


@pytest.mark.asyncio
async def test_metrics_endpoint_integration():
    """Integration test for /metrics endpoint."""
    # This would require a running PolyPy instance
    # Skipping for now, to be implemented with integration test suite
    pass
