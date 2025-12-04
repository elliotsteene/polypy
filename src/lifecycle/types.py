"""Type definitions for market lifecycle management."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(slots=True)
class MarketInfo:
    """Parsed market information from Gamma API."""

    condition_id: str
    question: str
    outcomes: list[str]
    tokens: list[dict]  # [{token_id: str, outcome: str}]
    end_date_iso: str
    end_timestamp: int  # Unix milliseconds
    active: bool
    closed: bool


# Callback signature: (event_name: str, asset_id: str) -> Awaitable[None]
LifecycleCallback = Callable[[str, str], Awaitable[None]]


# Configuration constants
DISCOVERY_INTERVAL = 60.0  # Seconds between market discovery
EXPIRATION_CHECK_INTERVAL = 30.0  # Seconds between expiration checks
CLEANUP_DELAY = 3600.0  # 1 hour grace period before removal
GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
