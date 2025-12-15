"""Worker request/response protocol for orderbook queries."""

from dataclasses import dataclass, field
from enum import IntEnum


class RequestType(IntEnum):
    """Request type enumeration."""

    GET_ORDERBOOK = 1


@dataclass(slots=True)
class OrderbookRequest:
    """Request for orderbook state."""

    request_id: str
    asset_id: str
    depth: int = 10


@dataclass(slots=True)
class OrderbookMetrics:
    """Calculated orderbook metrics."""

    spread: float | None
    mid_price: float | None
    imbalance: float  # bid_volume / (bid_volume + ask_volume), 0-1


@dataclass(slots=True)
class HistoryPoint:
    """Single history data point."""

    timestamp: int
    best_bid: int | None
    best_ask: int | None
    spread: int | None
    mid_price: int | None


@dataclass(slots=True)
class OrderbookResponse:
    """Response containing orderbook state."""

    request_id: str
    asset_id: str
    found: bool
    bids: list[tuple[float, float]] = field(
        default_factory=list
    )  # [(price, size), ...]
    asks: list[tuple[float, float]] = field(default_factory=list)
    metrics: OrderbookMetrics | None = None
    last_update_ts: int = 0
    history: list[HistoryPoint] = field(default_factory=list)
    error: str | None = None
