from src.worker.stats import WorkerStats


class TestWorkerStats:
    """Test WorkerStats dataclass."""

    def test_avg_processing_time_us_with_messages(self):
        """Test average processing time calculation with messages."""
        stats = WorkerStats(messages_processed=10, processing_time_ms=50.0)
        # (50ms * 1000) / 10 = 5000 microseconds
        assert stats.avg_processing_time_us == 5000.0

    def test_avg_processing_time_us_no_messages(self):
        """Test average processing time when no messages processed."""
        stats = WorkerStats(messages_processed=0, processing_time_ms=0.0)
        assert stats.avg_processing_time_us == 0.0

    def test_default_values(self):
        """Test WorkerStats default initialization."""
        stats = WorkerStats()
        assert stats.messages_processed == 0
        assert stats.updates_applied == 0
        assert stats.snapshots_received == 0
        assert stats.processing_time_ms == 0.0
        assert stats.last_message_ts == 0.0
        assert stats.orderbook_count == 0
        assert stats.memory_usage_bytes == 0
