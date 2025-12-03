import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class ConnectionStats:
    """Connection health and performance metrics"""

    messages_received: int = 0
    bytes_received: int = 0
    parse_errors: int = 0
    reconnect_count: int = 0
    last_message_ts: float = 0.0
    created_at: float = field(default_factory=time.monotonic)

    @property
    def uptime(self) -> float:
        """Seconds since creation"""
        return time.monotonic() - self.created_at

    @property
    def message_rate(self) -> float:
        """Messages per second (lifetime average)"""
        if self.uptime > 0:
            return self.messages_received / self.uptime

        return 0.0
