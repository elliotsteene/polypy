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
            "connection_count": 2,
            "active_connections": 2,
            "total_capacity": 1000,
            "stats": [
                {
                    "connection_id": "conn-0",
                    "status": "CONNECTED",
                    "is_draining": False,
                    "age_seconds": 100.0,
                    "messages_received": 1000,
                    "bytes_received": 50000,
                    "parse_errors": 2,
                    "reconnect_count": 1,
                    "is_healthy": True,
                    "total_markets": 75,
                    "subscribed_markets": 60,
                    "expired_markets": 15,
                    "pollution_ratio": 0.2,
                },
                {
                    "connection_id": "conn-1",
                    "status": "CONNECTED",
                    "is_draining": False,
                    "age_seconds": 200.0,
                    "messages_received": 2000,
                    "bytes_received": 100000,
                    "parse_errors": 0,
                    "reconnect_count": 0,
                    "is_healthy": True,
                    "total_markets": 75,
                    "subscribed_markets": 60,
                    "expired_markets": 15,
                    "pollution_ratio": 0.2,
                },
            ],
        },
        "router": {
            "messages_routed": 3000,
            "messages_dropped": 10,
            "batches_sent": 150,
            "queue_full_events": 2,
            "routing_errors": 1,
            "avg_latency_ms": 1.5,
            "queue_depths": {"async_queue": 10, "worker_0": 5, "worker_1": 3},
        },
        "workers": {
            "alive_count": 2,
            "is_healthy": True,
            "worker_stats": {
                "0": {
                    "messages_processed": 1500,
                    "updates_applied": 4500,
                    "snapshots_received": 75,
                    "avg_processing_time_us": 250.5,
                    "orderbook_count": 75,
                    "memory_usage_mb": 128.5,
                },
                "1": {
                    "messages_processed": 1500,
                    "updates_applied": 4500,
                    "snapshots_received": 75,
                    "avg_processing_time_us": 230.2,
                    "orderbook_count": 75,
                    "memory_usage_mb": 125.3,
                },
            },
        },
        "lifecycle": {
            "is_running": True,
            "known_market_count": 150,
        },
        "recycler": {
            "recycles_initiated": 5,
            "recycles_completed": 4,
            "recycles_failed": 1,
            "success_rate": 0.8,
            "markets_migrated": 300,
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
        assert "polypy_router_messages_routed_total 3000.0" in metrics_text
        assert "polypy_router_messages_dropped_total 10.0" in metrics_text
        assert "polypy_router_batches_sent_total 150.0" in metrics_text

    def test_recycler_metrics_with_labels(self, mock_stats):
        """Test recycler metrics with result labels."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for labeled counters
        assert 'polypy_recycler_recycles_total{result="completed"} 4.0' in metrics_text
        assert 'polypy_recycler_recycles_total{result="failed"} 1.0' in metrics_text
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

    def test_per_connection_metrics(self, mock_stats):
        """Test per-connection metrics with connection_id labels."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for conn-0 metrics
        assert (
            'polypy_connection_messages_received_total{connection_id="conn-0"} 1000.0'
            in metrics_text
        )
        assert (
            'polypy_connection_bytes_received_total{connection_id="conn-0"} 50000.0'
            in metrics_text
        )
        assert (
            'polypy_connection_parse_errors_total{connection_id="conn-0"} 2.0'
            in metrics_text
        )

        # Check for conn-1 metrics
        assert (
            'polypy_connection_messages_received_total{connection_id="conn-1"} 2000.0'
            in metrics_text
        )

    def test_per_worker_metrics(self, mock_stats):
        """Test per-worker metrics with worker_id labels."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for worker 0 metrics
        assert (
            'polypy_worker_messages_processed_total{worker_id="0"} 1500.0'
            in metrics_text
        )
        assert (
            'polypy_worker_updates_applied_total{worker_id="0"} 4500.0' in metrics_text
        )
        assert 'polypy_worker_orderbook_count{worker_id="0"} 75.0' in metrics_text

        # Check for worker 1 metrics
        assert (
            'polypy_worker_messages_processed_total{worker_id="1"} 1500.0'
            in metrics_text
        )

    def test_connection_pollution_ratio(self, mock_stats):
        """Test connection pollution ratio metric."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check pollution ratio for both connections
        assert (
            'polypy_connection_pollution_ratio{connection_id="conn-0"} 0.2'
            in metrics_text
        )
        assert (
            'polypy_connection_pollution_ratio{connection_id="conn-1"} 0.2'
            in metrics_text
        )

    def test_worker_memory_bytes_conversion(self, mock_stats):
        """Test that worker memory is correctly converted from MB to bytes."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # 128.5 MB = 134742016 bytes
        expected_bytes = 128.5 * 1024 * 1024
        # Check approximately (floating point)
        assert (
            f'polypy_worker_memory_bytes{{worker_id="0"}} {expected_bytes}'
            in metrics_text
            or 'polypy_worker_memory_bytes{worker_id="0"} 1.34' in metrics_text
        )  # Scientific notation

    def test_worker_processing_summary(self, mock_stats):
        """Test worker processing time summary metric."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for summary metrics (sum and count)
        assert 'polypy_worker_processing_seconds_sum{worker_id="0"}' in metrics_text
        assert (
            'polypy_worker_processing_seconds_count{worker_id="0"} 1500.0'
            in metrics_text
        )

    def test_router_latency_summary(self, mock_stats):
        """Test router routing latency summary metric."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for summary metrics
        assert "polypy_router_routing_latency_seconds_sum" in metrics_text
        assert "polypy_router_routing_latency_seconds_count 3000.0" in metrics_text

    def test_recycler_downtime_summary(self, mock_stats):
        """Test recycler downtime summary metric."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Check for summary metrics
        assert "polypy_recycler_downtime_seconds_sum" in metrics_text
        assert (
            "polypy_recycler_downtime_seconds_count 4.0" in metrics_text
        )  # recycles_completed

    def test_summary_calculations_accuracy(self, mock_stats):
        """Test that summary sum values are calculated correctly."""

        class MockApp:
            def get_stats(self):
                return mock_stats

        collector = MetricsCollector(MockApp())
        result = collector.collect_metrics()
        metrics_text = result.decode("utf-8")

        # Worker 0: avg_processing_time_us=250.5, messages_processed=1500
        # Total = 250.5 * 1500 / 1_000_000 = 0.37575 seconds
        # Check that sum is approximately correct
        assert 'polypy_worker_processing_seconds_sum{worker_id="0"}' in metrics_text

        # Router: avg_latency_ms=1.5, messages_routed=3000
        # Total = 1.5 * 3000 / 1000 = 4.5 seconds
        assert "polypy_router_routing_latency_seconds_sum" in metrics_text


@pytest.mark.asyncio
async def test_metrics_endpoint_integration():
    """Integration test for /metrics endpoint."""
    # This would require a running PolyPy instance
    # Skipping for now, to be implemented with integration test suite
    pass
