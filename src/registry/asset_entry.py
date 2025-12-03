import time
from dataclasses import dataclass, field
from enum import IntEnum, auto


class AssetStatus(IntEnum):
    """Market subscription status"""

    PENDING = auto()  # Awaiting subscription
    SUBSCRIBED = auto()  # Actively receiving updates
    EXPIRED = auto()  # Market ended, awaiting cleanup


@dataclass(slots=True)
class AssetEntry:
    """Single asset tracking entry"""

    asset_id: str
    condition_id: str  # Market identifier
    status: AssetStatus = AssetStatus.PENDING
    connection_id: str | None = None
    expiration_ts: int = 0  # Unix ms, 0 = unknown
    subscribed_at: float = 0.0  # monotonic time
    created_at: float = field(default_factory=time.monotonic)

    @property
    def is_active(self) -> bool:
        """Whether a market should receive updates"""
        return self.status == AssetStatus.SUBSCRIBED
