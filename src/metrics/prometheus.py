"""Prometheus metrics collector for PolyPy application stats.

Note on Summaries:
------------------
The Summary metrics (processing_seconds, routing_latency_seconds, downtime_seconds)
are populated from aggregate statistics (average * count) rather than individual
observations. This means:

1. Quantiles are not available (only _sum and _count)
2. The distribution is reconstructed from aggregates
3. For true percentile queries, use rate(_sum) / rate(_count) in PromQL

Example PromQL queries:
- Average processing time: rate(polypy_worker_processing_seconds_sum[1m]) / rate(polypy_worker_processing_seconds_count[1m])
- Total throughput: rate(polypy_worker_processing_seconds_count[1m])

To get true quantiles in the future, would need to track individual observations
in the stats collection layer, not just aggregate averages.
"""

from typing import TYPE_CHECKING, Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Summary,
    generate_latest,
)

if TYPE_CHECKING:
    from src.app import PolyPy


class MetricsCollector:
    """
    Collects application statistics and exposes them as Prometheus metrics.

    Generates fresh metrics on each collection by calling app.get_stats()
    and transforming the results into Prometheus format.
    """

    def __init__(self, app: "PolyPy") -> None:
        """Initialize metrics collector.

        Args:
            app: PolyPy application instance to collect stats from
        """
        self._app = app

    def collect_metrics(self) -> bytes:
        """
        Collect current stats and return Prometheus text format.

        Creates a fresh registry on each call and populates it with
        current application state.

        Returns:
            Prometheus text exposition format bytes
        """
        # Create fresh registry for this scrape
        registry = CollectorRegistry()

        # Get current stats
        stats = self._app.get_stats()

        # Create and populate metrics
        self._collect_application_metrics(registry, stats)
        self._collect_registry_metrics(registry, stats)
        self._collect_pool_metrics(registry, stats)
        self._collect_connection_metrics(registry, stats)
        self._collect_router_metrics(registry, stats)
        self._collect_worker_metrics(registry, stats)
        self._collect_worker_detail_metrics(registry, stats)
        self._collect_lifecycle_metrics(registry, stats)
        self._collect_recycler_metrics(registry, stats)

        # Generate Prometheus text format
        return generate_latest(registry)

    def _collect_application_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect application-level metrics."""
        running = Gauge(
            "polypy_application_running",
            "Whether the application is running (1) or stopped (0)",
            registry=registry,
        )
        running.set(1 if stats.get("running") else 0)

    def _collect_registry_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect asset registry metrics."""
        registry_stats = stats.get("registry", {})
        if not registry_stats:
            return

        # Market counts by status
        markets = Gauge(
            "polypy_registry_markets_total",
            "Number of markets by status",
            ["status"],
            registry=registry,
        )
        markets.labels(status="total").set(registry_stats.get("total_markets", 0))
        markets.labels(status="pending").set(registry_stats.get("pending", 0))
        markets.labels(status="subscribed").set(registry_stats.get("subscribed", 0))
        markets.labels(status="expired").set(registry_stats.get("expired", 0))

    def _collect_pool_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect connection pool metrics."""
        pool_stats = stats.get("pool", {})
        if not pool_stats:
            return

        # Connection counts
        connections = Gauge(
            "polypy_pool_connections",
            "Number of connections by type",
            ["type"],
            registry=registry,
        )
        connections.labels(type="total").set(pool_stats.get("connection_count", 0))
        connections.labels(type="active").set(pool_stats.get("active_connections", 0))

        # Pool capacity
        capacity = Gauge(
            "polypy_pool_capacity",
            "Total subscription capacity across all connections",
            registry=registry,
        )
        capacity.set(pool_stats.get("total_capacity", 0))

    def _collect_connection_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect per-connection metrics with connection_id labels."""
        pool_stats = stats.get("pool", {})
        if not pool_stats:
            return

        connection_stats = pool_stats.get("stats", [])
        if not connection_stats:
            return

        # Define metrics with connection_id label
        messages_received = Counter(
            "polypy_connection_messages_received_total",
            "Total messages received per connection",
            ["connection_id"],
            registry=registry,
        )

        bytes_received = Counter(
            "polypy_connection_bytes_received_total",
            "Total bytes received per connection",
            ["connection_id"],
            registry=registry,
        )

        parse_errors = Counter(
            "polypy_connection_parse_errors_total",
            "Total parse errors per connection",
            ["connection_id"],
            registry=registry,
        )

        reconnects = Counter(
            "polypy_connection_reconnects_total",
            "Total reconnection attempts per connection",
            ["connection_id"],
            registry=registry,
        )

        message_rate = Gauge(
            "polypy_connection_message_rate",
            "Message rate (messages/second) per connection",
            ["connection_id"],
            registry=registry,
        )

        healthy = Gauge(
            "polypy_connection_healthy",
            "Connection health status (1=healthy, 0=unhealthy)",
            ["connection_id", "status"],
            registry=registry,
        )

        markets_total = Gauge(
            "polypy_connection_markets_total",
            "Number of markets per connection by type",
            ["connection_id", "type"],
            registry=registry,
        )

        pollution_ratio = Gauge(
            "polypy_connection_pollution_ratio",
            "Ratio of expired to total markets per connection",
            ["connection_id"],
            registry=registry,
        )

        # Populate metrics for each connection
        for conn in connection_stats:
            conn_id = conn.get("connection_id", "unknown")

            # Counters
            messages_received.labels(connection_id=conn_id)._value.set(
                conn.get("messages_received", 0)
            )
            bytes_received.labels(connection_id=conn_id)._value.set(
                conn.get("bytes_received", 0)
            )
            parse_errors.labels(connection_id=conn_id)._value.set(
                conn.get("parse_errors", 0)
            )
            reconnects.labels(connection_id=conn_id)._value.set(
                conn.get("reconnect_count", 0)
            )

            # Gauges
            # Note: message_rate not available in stats dict, would need to add
            # For now, skip or compute from messages_received / age_seconds
            age_seconds = conn.get("age_seconds", 0)
            if age_seconds > 0:
                messages = conn.get("messages_received", 0)
                message_rate.labels(connection_id=conn_id).set(messages / age_seconds)

            # Health status
            status_name = conn.get("status", "UNKNOWN")
            is_healthy = conn.get("is_healthy", False)
            healthy.labels(connection_id=conn_id, status=status_name).set(
                1 if is_healthy else 0
            )

            # Market counts
            markets_total.labels(connection_id=conn_id, type="total").set(
                conn.get("total_markets", 0)
            )
            markets_total.labels(connection_id=conn_id, type="subscribed").set(
                conn.get("subscribed_markets", 0)
            )
            markets_total.labels(connection_id=conn_id, type="expired").set(
                conn.get("expired_markets", 0)
            )

            # Pollution ratio
            pollution_ratio.labels(connection_id=conn_id).set(
                conn.get("pollution_ratio", 0.0)
            )

    def _collect_router_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect message router metrics."""
        router_stats = stats.get("router", {})
        if not router_stats:
            return

        # Counters
        messages_routed = Counter(
            "polypy_router_messages_routed_total",
            "Total messages successfully routed to workers",
            registry=registry,
        )
        messages_routed._value.set(router_stats.get("messages_routed", 0))

        messages_dropped = Counter(
            "polypy_router_messages_dropped_total",
            "Total messages dropped due to backpressure",
            registry=registry,
        )
        messages_dropped._value.set(router_stats.get("messages_dropped", 0))

        batches_sent = Counter(
            "polypy_router_batches_sent_total",
            "Total batches sent to workers",
            registry=registry,
        )
        batches_sent._value.set(router_stats.get("batches_sent", 0))

        queue_full_events = Counter(
            "polypy_router_queue_full_events_total",
            "Total worker queue full events",
            registry=registry,
        )
        queue_full_events._value.set(router_stats.get("queue_full_events", 0))

        routing_errors = Counter(
            "polypy_router_routing_errors_total",
            "Total routing errors",
            registry=registry,
        )
        routing_errors._value.set(router_stats.get("routing_errors", 0))

        # Queue depths (gauges)
        queue_depths = router_stats.get("queue_depths", {})
        if queue_depths:
            queue_depth = Gauge(
                "polypy_router_queue_depth",
                "Current queue depth by queue name",
                ["queue_name"],
                registry=registry,
            )
            for queue_name, depth in queue_depths.items():
                if depth >= 0:  # Skip -1 values (NotImplementedError on macOS)
                    queue_depth.labels(queue_name=queue_name).set(depth)

        # Routing latency summary (replaces latency counter)
        routing_latency_summary = Summary(
            "polypy_router_routing_latency_seconds",
            "Message routing latency distribution in seconds",
            registry=registry,
        )

        # Populate summary from aggregate stats
        messages_routed_count = router_stats.get("messages_routed", 0)
        avg_latency_ms = router_stats.get("avg_latency_ms", 0.0)

        if messages_routed_count > 0 and avg_latency_ms > 0:
            avg_latency_seconds = avg_latency_ms / 1000.0
            total_latency_seconds = avg_latency_seconds * messages_routed_count

            routing_latency_summary._sum.set(total_latency_seconds)
            routing_latency_summary._count.set(messages_routed_count)

    def _collect_worker_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect worker process metrics."""
        worker_stats = stats.get("workers", {})
        if not worker_stats:
            return

        # Alive worker count
        alive_count = Gauge(
            "polypy_workers_alive",
            "Number of alive worker processes",
            registry=registry,
        )
        alive_count.set(worker_stats.get("alive_count", 0))

        # Healthy status
        healthy = Gauge(
            "polypy_workers_healthy",
            "Whether all workers are healthy (1) or not (0)",
            registry=registry,
        )
        healthy.set(1 if worker_stats.get("is_healthy") else 0)

    def _collect_worker_detail_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect detailed per-worker metrics with worker_id labels."""
        worker_stats = stats.get("workers", {})
        if not worker_stats:
            return

        worker_details = worker_stats.get("worker_stats", {})
        if not worker_details:
            return

        # Define metrics with worker_id label
        messages_processed = Counter(
            "polypy_worker_messages_processed_total",
            "Total messages processed per worker",
            ["worker_id"],
            registry=registry,
        )

        updates_applied = Counter(
            "polypy_worker_updates_applied_total",
            "Total orderbook updates applied per worker",
            ["worker_id"],
            registry=registry,
        )

        snapshots_received = Counter(
            "polypy_worker_snapshots_received_total",
            "Total orderbook snapshots received per worker",
            ["worker_id"],
            registry=registry,
        )

        orderbook_count = Gauge(
            "polypy_worker_orderbook_count",
            "Number of orderbooks managed per worker",
            ["worker_id"],
            registry=registry,
        )

        memory_bytes = Gauge(
            "polypy_worker_memory_bytes",
            "Memory usage in bytes per worker",
            ["worker_id"],
            registry=registry,
        )

        # Processing time summary (replaces avg_processing_time gauge)
        processing_time_summary = Summary(
            "polypy_worker_processing_seconds",
            "Worker message processing time distribution in seconds",
            ["worker_id"],
            registry=registry,
        )

        # Populate metrics for each worker
        for worker_id_str, worker_data in worker_details.items():
            worker_id = str(worker_id_str)

            # Counters
            messages_processed.labels(worker_id=worker_id)._value.set(
                worker_data.get("messages_processed", 0)
            )
            updates_applied.labels(worker_id=worker_id)._value.set(
                worker_data.get("updates_applied", 0)
            )
            snapshots_received.labels(worker_id=worker_id)._value.set(
                worker_data.get("snapshots_received", 0)
            )

            # Gauges
            orderbook_count.labels(worker_id=worker_id).set(
                worker_data.get("orderbook_count", 0)
            )

            # Memory: convert MB back to bytes for consistency
            memory_mb = worker_data.get("memory_usage_mb", 0.0)
            memory_bytes.labels(worker_id=worker_id).set(memory_mb * 1024 * 1024)

            # Processing time summary
            # Note: Since we only have avg_processing_time_us, we can't create
            # a true distribution. We'll track it as a counter+count pair.
            # In a future enhancement, we could store individual observations.
            avg_processing_us = worker_data.get("avg_processing_time_us", 0.0)
            messages_processed_count = worker_data.get("messages_processed", 0)

            if messages_processed_count > 0 and avg_processing_us > 0:
                # Set summary internal metrics directly
                avg_processing_seconds = avg_processing_us / 1_000_000.0
                total_processing_seconds = (
                    avg_processing_seconds * messages_processed_count
                )

                # Access summary internal metrics
                summary_metric = processing_time_summary.labels(worker_id=worker_id)
                summary_metric._sum.set(total_processing_seconds)
                summary_metric._count.set(messages_processed_count)

    def _collect_lifecycle_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect lifecycle controller metrics."""
        lifecycle_stats = stats.get("lifecycle", {})
        if not lifecycle_stats:
            return

        # Running status
        running = Gauge(
            "polypy_lifecycle_running",
            "Whether lifecycle controller is running (1) or stopped (0)",
            registry=registry,
        )
        running.set(1 if lifecycle_stats.get("is_running") else 0)

        # Known market count
        known_markets = Gauge(
            "polypy_lifecycle_known_markets",
            "Number of known market condition IDs",
            registry=registry,
        )
        known_markets.set(lifecycle_stats.get("known_market_count", 0))

    def _collect_recycler_metrics(
        self, registry: CollectorRegistry, stats: dict[str, Any]
    ) -> None:
        """Collect connection recycler metrics."""
        recycler_stats = stats.get("recycler", {})
        if not recycler_stats:
            return

        # Recycle operation counters by result
        recycles = Counter(
            "polypy_recycler_recycles_total",
            "Total recycle operations by result",
            ["result"],
            registry=registry,
        )
        recycles.labels(result="completed")._value.set(
            recycler_stats.get("recycles_completed", 0)
        )
        recycles.labels(result="failed")._value.set(
            recycler_stats.get("recycles_failed", 0)
        )

        # Markets migrated
        markets_migrated = Counter(
            "polypy_recycler_markets_migrated_total",
            "Total markets migrated during recycles",
            registry=registry,
        )
        markets_migrated._value.set(recycler_stats.get("markets_migrated", 0))

        # Success rate gauge
        success_rate = Gauge(
            "polypy_recycler_success_rate",
            "Recycle success rate (0.0 to 1.0)",
            registry=registry,
        )
        success_rate.set(recycler_stats.get("success_rate", 1.0))

        # Active recycles gauge
        active_recycles = Gauge(
            "polypy_recycler_active_recycles",
            "Number of currently active recycle operations",
            registry=registry,
        )
        active_list = recycler_stats.get("active_recycles", [])
        active_recycles.set(len(active_list))

        # Average downtime (convert ms to seconds)
        avg_downtime_ms = recycler_stats.get("avg_downtime_ms", 0)
        if avg_downtime_ms > 0:
            avg_downtime = Gauge(
                "polypy_recycler_avg_downtime_seconds",
                "Average downtime per recycle in seconds",
                registry=registry,
            )
            avg_downtime.set(avg_downtime_ms / 1000.0)

        # Downtime summary (complements avg_downtime gauge)
        downtime_summary = Summary(
            "polypy_recycler_downtime_seconds",
            "Recycle operation downtime distribution in seconds",
            registry=registry,
        )

        # Populate summary from aggregate stats
        recycles_completed = recycler_stats.get("recycles_completed", 0)
        avg_downtime_ms = recycler_stats.get("avg_downtime_ms", 0.0)

        if recycles_completed > 0 and avg_downtime_ms > 0:
            avg_downtime_seconds = avg_downtime_ms / 1000.0
            total_downtime_seconds = avg_downtime_seconds * recycles_completed

            downtime_summary._sum.set(total_downtime_seconds)
            downtime_summary._count.set(recycles_completed)
