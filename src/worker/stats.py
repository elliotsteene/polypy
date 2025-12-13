from dataclasses import dataclass


@dataclass(slots=True)
class WorkerStats:
    """Per-worker performance statistics."""

    messages_processed: int = 0
    updates_applied: int = 0
    snapshots_received: int = 0
    processing_time_ms: float = 0.0
    last_message_ts: float = 0.0
    orderbook_count: int = 0
    memory_usage_bytes: int = 0

    @property
    def avg_processing_time_us(self) -> float:
        """Average per-message processing time in microseconds."""
        if self.messages_processed > 0:
            return (self.processing_time_ms * 1000) / self.messages_processed
        return 0.0
