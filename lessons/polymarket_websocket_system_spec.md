# Polymarket WebSocket Subscription System - Technical Specification

## Document Purpose

This document provides a complete technical specification for building a high-performance, memory-efficient WebSocket subscription system for Polymarket prediction markets. The system must handle hundreds to thousands of concurrent market subscriptions with dynamic lifecycle management.

**Target Audience**: LLM agents building this system sequentially.

**Build Order**: Components are numbered and must be built in sequence. Each component includes acceptance criteria that must pass before proceeding.

---

## System Overview

### Core Problem

Polymarket's WebSocket API has critical constraints:
1. **500 market limit per connection** (undocumented but confirmed)
2. **No unsubscribe support** - once subscribed, markets cannot be removed from a connection
3. **Subscription only at connection open** - markets specified via initial message
4. **Requires ping every 10 seconds** to maintain connection

### Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ASYNC DOMAIN                                    │
│                         (Single Event Loop)                                  │
│                                                                              │
│  ┌─────────────┐    ┌─────────────────────┐    ┌──────────────────────┐    │
│  │   Market    │    │   Connection Pool   │    │   Message Router     │    │
│  │  Registry   │◄──►│      Manager        │───►│  (Async Queue)       │    │
│  └─────────────┘    └─────────────────────┘    └──────────┬───────────┘    │
│                              │                             │                 │
│                     ┌────────┼────────┐                    │                 │
│                     ▼        ▼        ▼                    │                 │
│                  ┌─────┐  ┌─────┐  ┌─────┐                 │                 │
│                  │Conn │  │Conn │  │Conn │                 │                 │
│                  │ #1  │  │ #2  │  │ #N  │                 │                 │
│                  └─────┘  └─────┘  └─────┘                 │                 │
└────────────────────────────────────────────────────────────┼─────────────────┘
                                                             │
                              ┌───────────────────────────────┘
                              │ multiprocessing.Queue (per worker)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PROCESS DOMAIN                                     │
│                    (Separate OS Processes)                                   │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Worker #1      │  │  Worker #2      │  │  Worker #N      │             │
│  │  ┌───────────┐  │  │  ┌───────────┐  │  │  ┌───────────┐  │             │
│  │  │ Orderbook │  │  │  │ Orderbook │  │  │  │ Orderbook │  │             │
│  │  │  States   │  │  │  │  States   │  │  │  │  States   │  │             │
│  │  │ (tokens   │  │  │  │ (tokens   │  │  │  │ (tokens   │  │             │
│  │  │  A,B,C)   │  │  │  │  D,E,F)   │  │  │  │  G,H,I)   │  │             │
│  │  └───────────┘  │  │  └───────────┘  │  │  └───────────┘  │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Performance Targets

| Metric | Target |
|--------|--------|
| Message routing latency | < 1ms |
| Orderbook update processing | < 5ms per update |
| Memory per orderbook | < 50KB typical |
| Connection recycling | Zero message loss |
| Reconnection time | < 5 seconds |

---

## API Reference (Polymarket WebSocket)

### Endpoint
```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

### Subscription Message (sent on connection open)
```json
{
  "assets_ids": ["token_id_1", "token_id_2", ...],
  "type": "market"
}
```

### Keepalive
Send string `"PING"` every 10 seconds.

### Message Types Received

#### 1. Book Snapshot (`event_type: "book"`)
Received on initial subscription and after trades.
```json
{
  "event_type": "book",
  "asset_id": "string",
  "market": "string (condition_id)",
  "timestamp": "string (unix ms)",
  "hash": "string",
  "bids": [{"price": "string", "size": "string"}, ...],
  "asks": [{"price": "string", "size": "string"}, ...]
}
```

#### 2. Price Change (`event_type: "price_change"`)
Received on order placement/cancellation.
```json
{
  "event_type": "price_change",
  "market": "string",
  "timestamp": "string",
  "price_changes": [
    {
      "asset_id": "string",
      "price": "string",
      "size": "string",
      "side": "BUY|SELL",
      "hash": "string"
    }
  ]
}
```

#### 3. Last Trade Price (`event_type: "last_trade_price"`)
Received when orders are matched.
```json
{
  "event_type": "last_trade_price",
  "asset_id": "string",
  "price": "string",
  "timestamp": "string"
}
```

---

## Component Build Sequence

### Phase 1: Core Data Structures
1. Component 1: Protocol Messages & Types
2. Component 2: Orderbook State Container
3. Component 3: Market Registry

### Phase 2: Connection Layer  
4. Component 4: Single WebSocket Connection Wrapper
5. Component 5: Connection Pool Manager

### Phase 3: Message Distribution
6. Component 6: Async Message Router
7. Component 7: Worker Process Manager

### Phase 4: Lifecycle Management
8. Component 8: Market Lifecycle Controller
9. Component 9: Connection Recycler

### Phase 5: Integration
10. Component 10: Main Application Orchestrator

---

## Component 1: Protocol Messages & Types

**File**: `src/protocol.py`

**Purpose**: Zero-copy, memory-efficient message parsing and type definitions.

### Requirements

1. Use `__slots__` on all dataclasses to eliminate `__dict__` overhead
2. Use `msgspec` for zero-copy JSON parsing (10-50x faster than json stdlib)
3. Define all message types as `msgspec.Struct` for memory efficiency
4. Prices/sizes stored as integers (multiply by 10^6) to avoid float overhead

### Implementation Specification

```python
"""
Protocol message definitions using msgspec for zero-copy parsing.

Key patterns:
- msgspec.Struct instead of dataclass (faster, less memory)
- __slots__ equivalent is automatic with Struct
- Frozen structs for immutability where appropriate
- Integer price representation: actual_price = price_int / 1_000_000
"""

import msgspec
from enum import IntEnum
from typing import Final

# Constants
PRICE_SCALE: Final[int] = 1_000_000  # 6 decimal places

class Side(IntEnum):
    """Order side as integer for fast comparison."""
    BUY = 0
    SELL = 1

class EventType(IntEnum):
    """Event type enumeration for switch-based dispatch."""
    BOOK = 0
    PRICE_CHANGE = 1
    LAST_TRADE_PRICE = 2
    TICK_SIZE_CHANGE = 3
    UNKNOWN = 255

# String to enum mapping for parsing
EVENT_TYPE_MAP: Final[dict[str, EventType]] = {
    "book": EventType.BOOK,
    "price_change": EventType.PRICE_CHANGE,
    "last_trade_price": EventType.LAST_TRADE_PRICE,
    "tick_size_change": EventType.TICK_SIZE_CHANGE,
}

class PriceLevel(msgspec.Struct, frozen=True, array_like=True):
    """
    Single price level in orderbook.
    
    array_like=True encodes as JSON array [price, size] saving bytes.
    frozen=True enables hashing and prevents mutation.
    """
    price: int  # Scaled integer
    size: int   # Scaled integer
    
    @classmethod
    def from_strings(cls, price: str, size: str) -> "PriceLevel":
        """Parse from API string format."""
        return cls(
            price=int(float(price) * PRICE_SCALE),
            size=int(float(size) * PRICE_SCALE)
        )

class PriceChange(msgspec.Struct, frozen=True):
    """Single price change event."""
    asset_id: str
    price: int
    size: int
    side: Side
    
class BookSnapshot(msgspec.Struct):
    """Full orderbook snapshot."""
    asset_id: str
    market: str
    timestamp: int  # Unix ms as integer
    hash: str
    bids: tuple[PriceLevel, ...]  # Tuple for immutability
    asks: tuple[PriceLevel, ...]

class PriceChangeEvent(msgspec.Struct):
    """Price change event with multiple changes."""
    market: str
    timestamp: int
    changes: tuple[PriceChange, ...]

class LastTradePrice(msgspec.Struct, frozen=True):
    """Last trade price event."""
    asset_id: str
    price: int
    timestamp: int

class ParsedMessage(msgspec.Struct):
    """
    Unified parsed message container.
    
    Uses tagged union pattern - event_type determines which field is populated.
    Only one of book/price_change/last_trade will be non-None.
    """
    event_type: EventType
    asset_id: str  # Primary routing key
    book: BookSnapshot | None = None
    price_change: PriceChangeEvent | None = None
    last_trade: LastTradePrice | None = None
    raw_timestamp: int = 0

# Pre-compiled decoder for raw JSON
_raw_decoder = msgspec.json.Decoder()

def parse_message(data: bytes) -> ParsedMessage | None:
    """
    Parse raw WebSocket message bytes into typed structure.
    
    Returns None for unparseable messages (logged separately).
    Uses single-pass parsing with early event_type detection.
    """
    try:
        raw = _raw_decoder.decode(data)
        
        event_type_str = raw.get("event_type", "")
        event_type = EVENT_TYPE_MAP.get(event_type_str, EventType.UNKNOWN)
        
        if event_type == EventType.UNKNOWN:
            return None
            
        asset_id = raw.get("asset_id", "")
        timestamp = int(raw.get("timestamp", 0))
        
        if event_type == EventType.BOOK:
            bids = tuple(
                PriceLevel.from_strings(level["price"], level["size"])
                for level in raw.get("bids", [])
            )
            asks = tuple(
                PriceLevel.from_strings(level["price"], level["size"])
                for level in raw.get("asks", [])
            )
            book = BookSnapshot(
                asset_id=asset_id,
                market=raw.get("market", ""),
                timestamp=timestamp,
                hash=raw.get("hash", ""),
                bids=bids,
                asks=asks
            )
            return ParsedMessage(
                event_type=event_type,
                asset_id=asset_id,
                book=book,
                raw_timestamp=timestamp
            )
            
        elif event_type == EventType.PRICE_CHANGE:
            changes = tuple(
                PriceChange(
                    asset_id=ch["asset_id"],
                    price=int(float(ch["price"]) * PRICE_SCALE),
                    size=int(float(ch["size"]) * PRICE_SCALE),
                    side=Side.BUY if ch["side"] == "BUY" else Side.SELL
                )
                for ch in raw.get("price_changes", [])
            )
            # For price_change, use first change's asset_id as primary
            primary_asset = changes[0].asset_id if changes else ""
            return ParsedMessage(
                event_type=event_type,
                asset_id=primary_asset,
                price_change=PriceChangeEvent(
                    market=raw.get("market", ""),
                    timestamp=timestamp,
                    changes=changes
                ),
                raw_timestamp=timestamp
            )
            
        elif event_type == EventType.LAST_TRADE_PRICE:
            return ParsedMessage(
                event_type=event_type,
                asset_id=asset_id,
                last_trade=LastTradePrice(
                    asset_id=asset_id,
                    price=int(float(raw.get("price", 0)) * PRICE_SCALE),
                    timestamp=timestamp
                ),
                raw_timestamp=timestamp
            )
            
    except Exception:
        return None
    
    return None
```

### Acceptance Criteria

1. `parse_message()` handles all three event types correctly
2. Memory usage per parsed message < 500 bytes
3. Parse throughput > 100,000 messages/second on single core
4. All structs are immutable where specified
5. Unit tests pass for malformed JSON (returns None, no exception)

---

## Component 2: Orderbook State Container

**File**: `src/orderbook.py`

**Purpose**: Memory-efficient orderbook state with O(log n) updates.

### Requirements

1. Use `sortedcontainers.SortedDict` for O(log n) price level operations
2. Store prices as integers (from Component 1)
3. Implement efficient delta application (not full replacement)
4. Track sequence/hash for consistency verification
5. Pre-allocate where possible

### Implementation Specification

```python
"""
Memory-efficient orderbook state container.

Design decisions:
- SortedDict for O(log n) insert/delete/lookup by price
- Integer prices as keys (no float comparison issues)
- Separate bid/ask dicts with opposite sort orders
- Hash tracking for consistency with exchange
"""

from sortedcontainers import SortedDict
from typing import Iterator
from dataclasses import dataclass, field
import time

from .protocol import (
    PriceLevel, BookSnapshot, PriceChange, Side, PRICE_SCALE
)

@dataclass(slots=True)
class OrderbookState:
    """
    Single orderbook state container.
    
    Slots usage saves ~200 bytes per instance vs regular dataclass.
    """
    asset_id: str
    market: str = ""
    
    # Bids: highest price first (negative keys for reverse sort)
    # Key: -price (negated for descending order)
    # Value: size at that price level
    _bids: SortedDict = field(default_factory=SortedDict)
    
    # Asks: lowest price first (natural ascending order)
    # Key: price
    # Value: size at that price level  
    _asks: SortedDict = field(default_factory=SortedDict)
    
    # Consistency tracking
    last_hash: str = ""
    last_update_ts: int = 0  # Unix ms
    local_update_ts: float = 0.0  # time.monotonic()
    update_count: int = 0
    
    # Cached computations (invalidated on update)
    _cached_best_bid: int | None = None
    _cached_best_ask: int | None = None
    _cache_valid: bool = False
    
    def apply_snapshot(self, snapshot: BookSnapshot) -> None:
        """
        Apply full book snapshot, replacing existing state.
        
        Called on initial subscription and after trades.
        """
        self._bids.clear()
        self._asks.clear()
        
        for level in snapshot.bids:
            if level.size > 0:
                self._bids[-level.price] = level.size  # Negate for desc sort
                
        for level in snapshot.asks:
            if level.size > 0:
                self._asks[level.price] = level.size
        
        self.market = snapshot.market
        self.last_hash = snapshot.hash
        self.last_update_ts = snapshot.timestamp
        self.local_update_ts = time.monotonic()
        self.update_count += 1
        self._invalidate_cache()
    
    def apply_price_change(self, change: PriceChange) -> None:
        """
        Apply single price level change.
        
        Size of 0 means remove the level.
        """
        if change.side == Side.BUY:
            key = -change.price  # Negated for bid ordering
            if change.size == 0:
                self._bids.pop(key, None)
            else:
                self._bids[key] = change.size
        else:
            key = change.price
            if change.size == 0:
                self._asks.pop(key, None)
            else:
                self._asks[key] = change.size
        
        self.local_update_ts = time.monotonic()
        self.update_count += 1
        self._invalidate_cache()
    
    def _invalidate_cache(self) -> None:
        """Mark cached values as stale."""
        self._cache_valid = False
        self._cached_best_bid = None
        self._cached_best_ask = None
    
    @property
    def best_bid(self) -> int | None:
        """Best (highest) bid price, cached."""
        if not self._cache_valid:
            self._recompute_cache()
        return self._cached_best_bid
    
    @property
    def best_ask(self) -> int | None:
        """Best (lowest) ask price, cached."""
        if not self._cache_valid:
            self._recompute_cache()
        return self._cached_best_ask
    
    def _recompute_cache(self) -> None:
        """Recompute cached best bid/ask."""
        if self._bids:
            # First key is most negative = highest price
            self._cached_best_bid = -self._bids.keys()[0]
        else:
            self._cached_best_bid = None
            
        if self._asks:
            self._cached_best_ask = self._asks.keys()[0]
        else:
            self._cached_best_ask = None
            
        self._cache_valid = True
    
    @property
    def spread(self) -> int | None:
        """Spread in scaled integer units, or None if no market."""
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None
    
    @property
    def mid_price(self) -> int | None:
        """Mid price in scaled integer units."""
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return (bid + ask) // 2
        return None
    
    def get_bids(self, depth: int = 10) -> list[PriceLevel]:
        """Get top N bid levels (highest first)."""
        result = []
        for neg_price, size in self._bids.items()[:depth]:
            result.append(PriceLevel(price=-neg_price, size=size))
        return result
    
    def get_asks(self, depth: int = 10) -> list[PriceLevel]:
        """Get top N ask levels (lowest first)."""
        result = []
        for price, size in self._asks.items()[:depth]:
            result.append(PriceLevel(price=price, size=size))
        return result
    
    @property
    def bid_depth(self) -> int:
        """Number of bid price levels."""
        return len(self._bids)
    
    @property
    def ask_depth(self) -> int:
        """Number of ask price levels."""
        return len(self._asks)
    
    @property
    def total_bid_size(self) -> int:
        """Sum of all bid sizes."""
        return sum(self._bids.values())
    
    @property
    def total_ask_size(self) -> int:
        """Sum of all ask sizes."""
        return sum(self._asks.values())
    
    @property
    def age_seconds(self) -> float:
        """Seconds since last update (monotonic)."""
        if self.local_update_ts == 0:
            return float('inf')
        return time.monotonic() - self.local_update_ts
    
    def __sizeof__(self) -> int:
        """Approximate memory usage in bytes."""
        # Base object + slots
        base = object.__sizeof__(self) + 8 * 10  # ~10 slot references
        # SortedDict overhead: ~64 bytes base + 16 bytes per entry
        bids_size = 64 + len(self._bids) * 16
        asks_size = 64 + len(self._asks) * 16
        # Strings
        str_size = len(self.asset_id) + len(self.market) + len(self.last_hash)
        return base + bids_size + asks_size + str_size


class OrderbookStore:
    """
    Collection of orderbooks with efficient lookup.
    
    Used by worker processes to manage multiple orderbook states.
    """
    __slots__ = ('_books', '_by_market')
    
    def __init__(self) -> None:
        self._books: dict[str, OrderbookState] = {}  # asset_id -> state
        self._by_market: dict[str, list[str]] = {}   # market -> [asset_ids]
    
    def get_or_create(self, asset_id: str) -> OrderbookState:
        """Get existing orderbook or create new one."""
        if asset_id not in self._books:
            self._books[asset_id] = OrderbookState(asset_id=asset_id)
        return self._books[asset_id]
    
    def get(self, asset_id: str) -> OrderbookState | None:
        """Get orderbook if exists."""
        return self._books.get(asset_id)
    
    def remove(self, asset_id: str) -> bool:
        """Remove orderbook, returns True if existed."""
        book = self._books.pop(asset_id, None)
        if book and book.market:
            market_assets = self._by_market.get(book.market, [])
            if asset_id in market_assets:
                market_assets.remove(asset_id)
        return book is not None
    
    def register_market(self, asset_id: str, market: str) -> None:
        """Register asset_id -> market mapping."""
        if market not in self._by_market:
            self._by_market[market] = []
        if asset_id not in self._by_market[market]:
            self._by_market[market].append(asset_id)
    
    def __len__(self) -> int:
        return len(self._books)
    
    def __iter__(self) -> Iterator[OrderbookState]:
        return iter(self._books.values())
    
    def memory_usage(self) -> int:
        """Total approximate memory usage."""
        return sum(book.__sizeof__() for book in self._books.values())
```

### Acceptance Criteria

1. `apply_snapshot()` correctly populates bid/ask levels
2. `apply_price_change()` correctly updates or removes levels
3. Bids sorted descending, asks sorted ascending
4. `best_bid`, `best_ask`, `spread`, `mid_price` compute correctly
5. Memory per orderbook < 50KB for 100 price levels each side
6. Update throughput > 500,000 ops/second

---

## Component 3: Market Registry

**File**: `src/registry.py`

**Purpose**: Central source of truth for market lifecycle and connection assignments.

### Requirements

1. Thread-safe for read access from multiple coroutines
2. Efficient lookup by asset_id, market (condition_id), connection_id
3. Track market status: PENDING, SUBSCRIBED, EXPIRED
4. Support batch operations for connection recycling
5. Expiration time tracking with sorted index

### Implementation Specification

```python
"""
Market Registry - Central coordination for market lifecycle.

Thread-safety model:
- Single writer (lifecycle controller)
- Multiple readers (connection pool, message router)
- Uses asyncio.Lock for mutations
- Read-only operations are lock-free (dict/set operations are atomic in CPython)
"""

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Iterator, Set, FrozenSet
from sortedcontainers import SortedDict
import time

class MarketStatus(IntEnum):
    """Market subscription status."""
    PENDING = auto()      # Awaiting subscription
    SUBSCRIBED = auto()   # Actively receiving updates
    EXPIRED = auto()      # Market ended, awaiting cleanup

@dataclass(slots=True)
class MarketEntry:
    """Single market tracking entry."""
    asset_id: str
    condition_id: str  # Market identifier
    status: MarketStatus = MarketStatus.PENDING
    connection_id: str | None = None
    expiration_ts: int = 0  # Unix ms, 0 = unknown
    subscribed_at: float = 0.0  # monotonic time
    created_at: float = field(default_factory=time.monotonic)
    
    @property
    def is_active(self) -> bool:
        """Whether market should receive updates."""
        return self.status == MarketStatus.SUBSCRIBED


class MarketRegistry:
    """
    Central registry for all tracked markets.
    
    Provides efficient multi-index access:
    - By asset_id (primary key)
    - By condition_id (market grouping)
    - By connection_id (for recycling)
    - By expiration time (for lifecycle)
    - By status (for queries)
    """
    
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        
        # Primary storage
        self._markets: dict[str, MarketEntry] = {}
        
        # Secondary indices
        self._by_condition: dict[str, set[str]] = {}  # condition_id -> {asset_ids}
        self._by_connection: dict[str, set[str]] = {}  # connection_id -> {asset_ids}
        self._by_status: dict[MarketStatus, set[str]] = {
            status: set() for status in MarketStatus
        }
        
        # Expiration index: expiration_ts -> {asset_ids}
        # SortedDict for efficient "get all expiring before X" queries
        self._by_expiration: SortedDict[int, set[str]] = SortedDict()
        
        # Pending queue for batch subscription
        self._pending_queue: list[str] = []
    
    # ─────────────────────────────────────────────────────────────────
    # Read-only operations (lock-free)
    # ─────────────────────────────────────────────────────────────────
    
    def get(self, asset_id: str) -> MarketEntry | None:
        """Get market entry by asset_id."""
        return self._markets.get(asset_id)
    
    def __contains__(self, asset_id: str) -> bool:
        return asset_id in self._markets
    
    def __len__(self) -> int:
        return len(self._markets)
    
    def get_by_condition(self, condition_id: str) -> FrozenSet[str]:
        """Get all asset_ids for a market condition."""
        return frozenset(self._by_condition.get(condition_id, set()))
    
    def get_by_connection(self, connection_id: str) -> FrozenSet[str]:
        """Get all asset_ids assigned to a connection."""
        return frozenset(self._by_connection.get(connection_id, set()))
    
    def get_active_by_connection(self, connection_id: str) -> FrozenSet[str]:
        """Get only active (non-expired) asset_ids for a connection."""
        all_assets = self._by_connection.get(connection_id, set())
        active = self._by_status.get(MarketStatus.SUBSCRIBED, set())
        return frozenset(all_assets & active)
    
    def get_by_status(self, status: MarketStatus) -> FrozenSet[str]:
        """Get all asset_ids with given status."""
        return frozenset(self._by_status.get(status, set()))
    
    def get_pending_count(self) -> int:
        """Number of markets awaiting subscription."""
        return len(self._pending_queue)
    
    def get_expiring_before(self, timestamp: int) -> list[str]:
        """Get all asset_ids expiring before given timestamp."""
        result = []
        for exp_ts, asset_ids in self._by_expiration.items():
            if exp_ts >= timestamp:
                break
            result.extend(asset_ids)
        return result
    
    def connection_stats(self, connection_id: str) -> dict:
        """Get statistics for a connection."""
        assets = self._by_connection.get(connection_id, set())
        if not assets:
            return {"total": 0, "active": 0, "expired": 0, "pollution_ratio": 0.0}
        
        active = sum(
            1 for aid in assets 
            if self._markets.get(aid, MarketEntry("")).status == MarketStatus.SUBSCRIBED
        )
        expired = sum(
            1 for aid in assets
            if self._markets.get(aid, MarketEntry("")).status == MarketStatus.EXPIRED
        )
        total = len(assets)
        
        return {
            "total": total,
            "active": active,
            "expired": expired,
            "pollution_ratio": expired / total if total > 0 else 0.0
        }
    
    # ─────────────────────────────────────────────────────────────────
    # Mutation operations (require lock)
    # ─────────────────────────────────────────────────────────────────
    
    async def add_market(
        self, 
        asset_id: str, 
        condition_id: str,
        expiration_ts: int = 0
    ) -> bool:
        """
        Add new market to registry.
        
        Returns False if market already exists.
        """
        if asset_id in self._markets:
            return False
            
        async with self._lock:
            # Double-check under lock
            if asset_id in self._markets:
                return False
            
            entry = MarketEntry(
                asset_id=asset_id,
                condition_id=condition_id,
                status=MarketStatus.PENDING,
                expiration_ts=expiration_ts
            )
            
            # Primary storage
            self._markets[asset_id] = entry
            
            # Update indices
            if condition_id not in self._by_condition:
                self._by_condition[condition_id] = set()
            self._by_condition[condition_id].add(asset_id)
            
            self._by_status[MarketStatus.PENDING].add(asset_id)
            
            if expiration_ts > 0:
                if expiration_ts not in self._by_expiration:
                    self._by_expiration[expiration_ts] = set()
                self._by_expiration[expiration_ts].add(asset_id)
            
            self._pending_queue.append(asset_id)
            
            return True
    
    async def mark_subscribed(
        self, 
        asset_ids: list[str], 
        connection_id: str
    ) -> int:
        """
        Mark markets as subscribed to a connection.
        
        Returns count of successfully updated markets.
        """
        async with self._lock:
            count = 0
            now = time.monotonic()
            
            if connection_id not in self._by_connection:
                self._by_connection[connection_id] = set()
            
            for asset_id in asset_ids:
                entry = self._markets.get(asset_id)
                if entry is None:
                    continue
                    
                # Update status index
                old_status = entry.status
                self._by_status[old_status].discard(asset_id)
                self._by_status[MarketStatus.SUBSCRIBED].add(asset_id)
                
                # Update entry
                entry.status = MarketStatus.SUBSCRIBED
                entry.connection_id = connection_id
                entry.subscribed_at = now
                
                # Update connection index
                self._by_connection[connection_id].add(asset_id)
                
                # Remove from pending queue
                if asset_id in self._pending_queue:
                    self._pending_queue.remove(asset_id)
                
                count += 1
            
            return count
    
    async def mark_expired(self, asset_ids: list[str]) -> int:
        """
        Mark markets as expired.
        
        Does not remove from connection - they're still subscribed,
        just no longer active for routing.
        """
        async with self._lock:
            count = 0
            
            for asset_id in asset_ids:
                entry = self._markets.get(asset_id)
                if entry is None or entry.status == MarketStatus.EXPIRED:
                    continue
                
                old_status = entry.status
                self._by_status[old_status].discard(asset_id)
                self._by_status[MarketStatus.EXPIRED].add(asset_id)
                entry.status = MarketStatus.EXPIRED
                count += 1
            
            return count
    
    async def take_pending_batch(self, max_size: int = 400) -> list[str]:
        """
        Take a batch of pending markets for subscription.
        
        Removes from pending queue but doesn't change status yet.
        """
        async with self._lock:
            batch = self._pending_queue[:max_size]
            self._pending_queue = self._pending_queue[max_size:]
            return batch
    
    async def reassign_connection(
        self,
        asset_ids: list[str],
        old_connection_id: str,
        new_connection_id: str
    ) -> int:
        """
        Move markets from one connection to another.
        
        Used during connection recycling.
        """
        async with self._lock:
            count = 0
            
            if new_connection_id not in self._by_connection:
                self._by_connection[new_connection_id] = set()
            
            for asset_id in asset_ids:
                entry = self._markets.get(asset_id)
                if entry is None or entry.connection_id != old_connection_id:
                    continue
                
                # Update connection index
                self._by_connection[old_connection_id].discard(asset_id)
                self._by_connection[new_connection_id].add(asset_id)
                
                # Update entry
                entry.connection_id = new_connection_id
                count += 1
            
            return count
    
    async def remove_connection(self, connection_id: str) -> set[str]:
        """
        Remove connection from registry.
        
        Returns set of asset_ids that were assigned.
        Markets are NOT removed - they become orphaned (connection_id=None).
        """
        async with self._lock:
            assets = self._by_connection.pop(connection_id, set())
            
            for asset_id in assets:
                entry = self._markets.get(asset_id)
                if entry and entry.connection_id == connection_id:
                    entry.connection_id = None
            
            return assets
    
    async def remove_market(self, asset_id: str) -> bool:
        """
        Fully remove a market from the registry.
        
        Used for cleanup of long-expired markets.
        """
        async with self._lock:
            entry = self._markets.pop(asset_id, None)
            if entry is None:
                return False
            
            # Clean up all indices
            self._by_condition.get(entry.condition_id, set()).discard(asset_id)
            if entry.connection_id:
                self._by_connection.get(entry.connection_id, set()).discard(asset_id)
            self._by_status[entry.status].discard(asset_id)
            
            if entry.expiration_ts in self._by_expiration:
                self._by_expiration[entry.expiration_ts].discard(asset_id)
                if not self._by_expiration[entry.expiration_ts]:
                    del self._by_expiration[entry.expiration_ts]
            
            if asset_id in self._pending_queue:
                self._pending_queue.remove(asset_id)
            
            return True
    
    # ─────────────────────────────────────────────────────────────────
    # Iteration
    # ─────────────────────────────────────────────────────────────────
    
    def iter_active(self) -> Iterator[MarketEntry]:
        """Iterate over all active (subscribed) markets."""
        for asset_id in self._by_status[MarketStatus.SUBSCRIBED]:
            entry = self._markets.get(asset_id)
            if entry:
                yield entry
    
    def iter_all(self) -> Iterator[MarketEntry]:
        """Iterate over all markets."""
        yield from self._markets.values()
```

### Acceptance Criteria

1. All read operations are lock-free and safe for concurrent access
2. All mutation operations properly maintain index consistency
3. `get_expiring_before()` returns correct results
4. `connection_stats()` accurately reports pollution ratio
5. `take_pending_batch()` returns correct batch sizes
6. Stress test: 10,000 markets with 100 concurrent readers passes

---

## Component 4: Single WebSocket Connection Wrapper

**File**: `src/connection.py`

**Purpose**: Manages single WebSocket connection lifecycle with reconnection.

### Requirements

1. Use `websockets` library (asyncio-native)
2. Automatic ping every 10 seconds
3. Exponential backoff reconnection (1s, 2s, 4s, 8s, max 30s)
4. Message callback for parsed messages
5. Health monitoring (latency, error count)
6. Graceful shutdown

### Implementation Specification

```python
"""
WebSocket connection wrapper with automatic lifecycle management.

Key behaviors:
- Connects and subscribes to asset_ids on start
- Maintains heartbeat ping every 10 seconds
- Automatic reconnection with exponential backoff
- Emits parsed messages via callback
- Tracks health metrics
"""

import asyncio
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import (
    ConnectionClosed, 
    ConnectionClosedError,
    ConnectionClosedOK
)
import json
import time
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Callable, Awaitable, Set, FrozenSet
import logging

from .protocol import ParsedMessage, parse_message

logger = logging.getLogger(__name__)

# Constants
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL = 10.0  # seconds
PING_TIMEOUT = 30.0   # seconds before considering connection dead
MAX_RECONNECT_DELAY = 30.0
INITIAL_RECONNECT_DELAY = 1.0


class ConnectionStatus(IntEnum):
    """Connection lifecycle status."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    DRAINING = auto()  # Marked for recycling, no new subscriptions
    CLOSED = auto()     # Permanently closed


@dataclass(slots=True)
class ConnectionStats:
    """Connection health and performance metrics."""
    messages_received: int = 0
    bytes_received: int = 0
    parse_errors: int = 0
    reconnect_count: int = 0
    last_message_ts: float = 0.0
    last_ping_ts: float = 0.0
    last_pong_ts: float = 0.0
    created_at: float = field(default_factory=time.monotonic)
    
    @property
    def uptime(self) -> float:
        """Seconds since creation."""
        return time.monotonic() - self.created_at
    
    @property
    def message_rate(self) -> float:
        """Messages per second (lifetime average)."""
        if self.uptime > 0:
            return self.messages_received / self.uptime
        return 0.0
    
    @property
    def latency_estimate(self) -> float | None:
        """Estimated latency based on ping/pong."""
        if self.last_pong_ts > 0 and self.last_ping_ts > 0:
            return self.last_pong_ts - self.last_ping_ts
        return None


# Type alias for message callback
MessageCallback = Callable[[str, ParsedMessage], Awaitable[None]]
# Args: (connection_id, parsed_message)


class WebSocketConnection:
    """
    Single managed WebSocket connection.
    
    Lifecycle:
    1. Create with asset_ids and callback
    2. Call start() to begin connection
    3. Messages flow via callback
    4. Call stop() for graceful shutdown
    """
    
    __slots__ = (
        'connection_id', 'asset_ids', 'on_message', 
        '_ws', '_status', '_stats', '_stop_event',
        '_receive_task', '_ping_task', '_reconnect_delay',
        '_subscribed_asset_ids'
    )
    
    def __init__(
        self,
        connection_id: str,
        asset_ids: list[str],
        on_message: MessageCallback
    ) -> None:
        """
        Initialize connection.
        
        Args:
            connection_id: Unique identifier for this connection
            asset_ids: List of token IDs to subscribe to (max 500)
            on_message: Async callback for parsed messages
        """
        if len(asset_ids) > 500:
            raise ValueError(f"Cannot subscribe to more than 500 markets, got {len(asset_ids)}")
        
        self.connection_id = connection_id
        self.asset_ids = list(asset_ids)  # Copy to prevent mutation
        self.on_message = on_message
        
        self._ws: WebSocketClientProtocol | None = None
        self._status = ConnectionStatus.DISCONNECTED
        self._stats = ConnectionStats()
        self._stop_event = asyncio.Event()
        self._receive_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._reconnect_delay = INITIAL_RECONNECT_DELAY
        self._subscribed_asset_ids: Set[str] = set()
    
    @property
    def status(self) -> ConnectionStatus:
        return self._status
    
    @property
    def stats(self) -> ConnectionStats:
        return self._stats
    
    @property
    def subscribed_assets(self) -> FrozenSet[str]:
        return frozenset(self._subscribed_asset_ids)
    
    @property
    def is_healthy(self) -> bool:
        """Check if connection appears healthy."""
        if self._status != ConnectionStatus.CONNECTED:
            return False
        
        # Check for stale connection (no messages in 60s)
        if self._stats.last_message_ts > 0:
            silence = time.monotonic() - self._stats.last_message_ts
            if silence > 60.0:
                return False
        
        return True
    
    async def start(self) -> None:
        """Start connection and message processing."""
        if self._status not in (ConnectionStatus.DISCONNECTED, ConnectionStatus.CLOSED):
            return
        
        self._stop_event.clear()
        self._status = ConnectionStatus.CONNECTING
        
        # Start connection loop
        self._receive_task = asyncio.create_task(
            self._connection_loop(),
            name=f"ws-{self.connection_id}-recv"
        )
    
    async def stop(self) -> None:
        """Gracefully stop connection."""
        self._status = ConnectionStatus.CLOSED
        self._stop_event.set()
        
        # Cancel tasks
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    def mark_draining(self) -> None:
        """Mark connection as draining (for recycling)."""
        if self._status == ConnectionStatus.CONNECTED:
            self._status = ConnectionStatus.DRAINING
    
    async def _connection_loop(self) -> None:
        """Main connection loop with reconnection logic."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Connection {self.connection_id} error: {e}",
                    exc_info=True
                )
            
            # Reconnect with backoff if not stopped
            if not self._stop_event.is_set() and self._status != ConnectionStatus.CLOSED:
                self._status = ConnectionStatus.DISCONNECTED
                self._stats.reconnect_count += 1
                
                logger.info(
                    f"Connection {self.connection_id} reconnecting in "
                    f"{self._reconnect_delay:.1f}s (attempt {self._stats.reconnect_count})"
                )
                
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reconnect_delay
                    )
                    # Stop event was set
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, proceed to reconnect
                    pass
                
                # Exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    MAX_RECONNECT_DELAY
                )
        
        self._status = ConnectionStatus.CLOSED
    
    async def _connect_and_run(self) -> None:
        """Establish connection and run message loop."""
        self._status = ConnectionStatus.CONNECTING
        
        async with websockets.connect(
            WS_URL,
            ping_interval=None,  # We handle our own pings
            ping_timeout=None,
            close_timeout=5.0,
            max_size=10 * 1024 * 1024,  # 10MB max message
        ) as ws:
            self._ws = ws
            
            # Send subscription message
            subscribe_msg = json.dumps({
                "assets_ids": self.asset_ids,
                "type": "market"
            })
            await ws.send(subscribe_msg)
            self._subscribed_asset_ids = set(self.asset_ids)
            
            self._status = ConnectionStatus.CONNECTED
            self._reconnect_delay = INITIAL_RECONNECT_DELAY  # Reset backoff
            
            logger.info(
                f"Connection {self.connection_id} established, "
                f"subscribed to {len(self.asset_ids)} markets"
            )
            
            # Start ping task
            self._ping_task = asyncio.create_task(
                self._ping_loop(),
                name=f"ws-{self.connection_id}-ping"
            )
            
            # Message receive loop
            try:
                await self._receive_loop(ws)
            finally:
                if self._ping_task:
                    self._ping_task.cancel()
                    try:
                        await self._ping_task
                    except asyncio.CancelledError:
                        pass
    
    async def _receive_loop(self, ws: WebSocketClientProtocol) -> None:
        """Receive and process messages."""
        async for message in ws:
            if self._stop_event.is_set():
                break
            
            # Track stats
            now = time.monotonic()
            self._stats.last_message_ts = now
            
            if isinstance(message, bytes):
                self._stats.bytes_received += len(message)
                data = message
            else:
                data = message.encode('utf-8')
                self._stats.bytes_received += len(data)
            
            self._stats.messages_received += 1
            
            # Handle PONG response
            if data == b"PONG" or message == "PONG":
                self._stats.last_pong_ts = now
                continue
            
            # Parse message
            parsed = parse_message(data)
            if parsed is None:
                self._stats.parse_errors += 1
                continue
            
            # Emit via callback
            try:
                await self.on_message(self.connection_id, parsed)
            except Exception as e:
                logger.error(
                    f"Message callback error: {e}",
                    exc_info=True
                )
    
    async def _ping_loop(self) -> None:
        """Send periodic pings."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(PING_INTERVAL)
                
                if self._ws and self._status in (
                    ConnectionStatus.CONNECTED, 
                    ConnectionStatus.DRAINING
                ):
                    self._stats.last_ping_ts = time.monotonic()
                    await self._ws.send("PING")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Ping error: {e}")
```

### Acceptance Criteria

1. Connection establishes and subscribes to provided asset_ids
2. Ping sent every 10 seconds
3. Reconnects on disconnection with exponential backoff
4. Messages properly parsed and emitted via callback
5. Graceful shutdown completes within 5 seconds
6. Stats accurately track messages, bytes, reconnects
7. Health check detects stale connections (>60s silence)

---

## Component 5: Connection Pool Manager

**File**: `src/pool.py`

**Purpose**: Manages multiple connections with capacity tracking and recycling.

### Requirements

1. Track connection capacity (target 400 active per connection)
2. Assign new markets to connections with available capacity
3. Create new connections when capacity exhausted
4. Track pollution ratio per connection
5. Coordinate with registry for market assignments
6. Support recycling workflow

### Implementation Specification

```python
"""
Connection Pool Manager - Orchestrates multiple WebSocket connections.

Responsibilities:
- Create/destroy connections based on demand
- Track capacity and pollution per connection
- Assign markets to appropriate connections
- Coordinate recycling of polluted connections
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Set, FrozenSet, Callable, Awaitable
import uuid
import logging
import time

from .connection import WebSocketConnection, ConnectionStatus, MessageCallback
from .registry import MarketRegistry, MarketStatus
from .protocol import ParsedMessage

logger = logging.getLogger(__name__)

# Configuration
TARGET_MARKETS_PER_CONNECTION = 400  # Leave headroom below 500 limit
MAX_MARKETS_PER_CONNECTION = 500
POLLUTION_THRESHOLD = 0.30  # 30% expired triggers recycling
MIN_AGE_FOR_RECYCLING = 300.0  # Don't recycle connections younger than 5 min
BATCH_SUBSCRIPTION_INTERVAL = 30.0  # Check for pending markets every 30s
MIN_PENDING_FOR_NEW_CONNECTION = 50  # Min pending to justify new connection


@dataclass(slots=True)
class ConnectionInfo:
    """Metadata about a managed connection."""
    connection: WebSocketConnection
    created_at: float = field(default_factory=time.monotonic)
    is_draining: bool = False
    
    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at


class ConnectionPool:
    """
    Manages pool of WebSocket connections.
    
    Thread-safety: All methods are async and safe for concurrent calls
    from the same event loop.
    """
    
    __slots__ = (
        '_registry', '_message_callback', '_connections',
        '_subscription_task', '_running', '_lock'
    )
    
    def __init__(
        self,
        registry: MarketRegistry,
        message_callback: MessageCallback
    ) -> None:
        """
        Initialize pool.
        
        Args:
            registry: Market registry for coordination
            message_callback: Callback for all received messages
        """
        self._registry = registry
        self._message_callback = message_callback
        self._connections: Dict[str, ConnectionInfo] = {}
        self._subscription_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
    
    @property
    def connection_count(self) -> int:
        return len(self._connections)
    
    @property
    def active_connection_count(self) -> int:
        return sum(
            1 for info in self._connections.values()
            if info.connection.status == ConnectionStatus.CONNECTED
            and not info.is_draining
        )
    
    def get_total_capacity(self) -> int:
        """Total subscription slots available."""
        return sum(
            TARGET_MARKETS_PER_CONNECTION - len(self._registry.get_by_connection(cid))
            for cid, info in self._connections.items()
            if not info.is_draining
        )
    
    async def start(self) -> None:
        """Start the pool and subscription manager."""
        self._running = True
        
        # Start subscription management task
        self._subscription_task = asyncio.create_task(
            self._subscription_loop(),
            name="pool-subscription-manager"
        )
        
        logger.info("Connection pool started")
    
    async def stop(self) -> None:
        """Stop all connections and cleanup."""
        self._running = False
        
        # Stop subscription task
        if self._subscription_task:
            self._subscription_task.cancel()
            try:
                await self._subscription_task
            except asyncio.CancelledError:
                pass
        
        # Stop all connections concurrently
        stop_tasks = [
            info.connection.stop()
            for info in self._connections.values()
        ]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        self._connections.clear()
        logger.info("Connection pool stopped")
    
    async def _subscription_loop(self) -> None:
        """Periodically process pending markets."""
        while self._running:
            try:
                await asyncio.sleep(BATCH_SUBSCRIPTION_INTERVAL)
                await self._process_pending_markets()
                await self._check_for_recycling()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Subscription loop error: {e}", exc_info=True)
    
    async def _process_pending_markets(self) -> None:
        """Create connections for pending markets."""
        pending_count = self._registry.get_pending_count()
        
        if pending_count < MIN_PENDING_FOR_NEW_CONNECTION:
            return
        
        async with self._lock:
            # Take batch of pending markets
            batch = await self._registry.take_pending_batch(TARGET_MARKETS_PER_CONNECTION)
            
            if not batch:
                return
            
            # Create new connection
            connection_id = f"conn-{uuid.uuid4().hex[:8]}"
            connection = WebSocketConnection(
                connection_id=connection_id,
                asset_ids=batch,
                on_message=self._message_callback
            )
            
            self._connections[connection_id] = ConnectionInfo(connection=connection)
            
            # Start connection
            await connection.start()
            
            # Mark markets as subscribed
            await self._registry.mark_subscribed(batch, connection_id)
            
            logger.info(
                f"Created connection {connection_id} with {len(batch)} markets "
                f"(total connections: {len(self._connections)})"
            )
    
    async def _check_for_recycling(self) -> None:
        """Check connections for recycling needs."""
        for connection_id, info in list(self._connections.items()):
            if info.is_draining:
                continue
            
            # Skip young connections
            if info.age < MIN_AGE_FOR_RECYCLING:
                continue
            
            stats = self._registry.connection_stats(connection_id)
            
            if stats["pollution_ratio"] >= POLLUTION_THRESHOLD:
                logger.info(
                    f"Connection {connection_id} needs recycling: "
                    f"{stats['pollution_ratio']:.1%} pollution "
                    f"({stats['expired']}/{stats['total']} expired)"
                )
                # Recycling handled by Component 9
                asyncio.create_task(
                    self._initiate_recycling(connection_id),
                    name=f"recycle-{connection_id}"
                )
    
    async def _initiate_recycling(self, connection_id: str) -> None:
        """
        Initiate connection recycling.
        
        Placeholder - full implementation in Component 9.
        """
        info = self._connections.get(connection_id)
        if not info:
            return
        
        info.is_draining = True
        info.connection.mark_draining()
        
        # Get active markets to migrate
        active_assets = list(self._registry.get_active_by_connection(connection_id))
        
        if not active_assets:
            # No active markets, just close
            await self._remove_connection(connection_id)
            return
        
        # Create replacement connection
        new_connection_id = f"conn-{uuid.uuid4().hex[:8]}"
        new_connection = WebSocketConnection(
            connection_id=new_connection_id,
            asset_ids=active_assets,
            on_message=self._message_callback
        )
        
        async with self._lock:
            self._connections[new_connection_id] = ConnectionInfo(connection=new_connection)
        
        # Start new connection
        await new_connection.start()
        
        # Wait for connection to stabilize
        await asyncio.sleep(2.0)
        
        # Reassign markets in registry
        await self._registry.reassign_connection(
            active_assets,
            connection_id,
            new_connection_id
        )
        
        # Remove old connection
        await self._remove_connection(connection_id)
        
        logger.info(
            f"Recycled {connection_id} -> {new_connection_id} "
            f"with {len(active_assets)} active markets"
        )
    
    async def _remove_connection(self, connection_id: str) -> None:
        """Remove and cleanup a connection."""
        async with self._lock:
            info = self._connections.pop(connection_id, None)
        
        if info:
            await info.connection.stop()
            await self._registry.remove_connection(connection_id)
    
    async def force_subscribe(self, asset_ids: list[str]) -> str:
        """
        Immediately create connection for given assets.
        
        Bypasses pending queue - used for priority subscriptions.
        Returns connection_id.
        """
        if len(asset_ids) > MAX_MARKETS_PER_CONNECTION:
            raise ValueError(f"Cannot subscribe to more than {MAX_MARKETS_PER_CONNECTION} markets")
        
        connection_id = f"conn-{uuid.uuid4().hex[:8]}"
        connection = WebSocketConnection(
            connection_id=connection_id,
            asset_ids=asset_ids,
            on_message=self._message_callback
        )
        
        async with self._lock:
            self._connections[connection_id] = ConnectionInfo(connection=connection)
        
        await connection.start()
        await self._registry.mark_subscribed(asset_ids, connection_id)
        
        return connection_id
    
    def get_connection_stats(self) -> list[dict]:
        """Get stats for all connections."""
        result = []
        for cid, info in self._connections.items():
            conn_stats = info.connection.stats
            registry_stats = self._registry.connection_stats(cid)
            
            result.append({
                "connection_id": cid,
                "status": info.connection.status.name,
                "is_draining": info.is_draining,
                "age_seconds": info.age,
                "messages_received": conn_stats.messages_received,
                "reconnect_count": conn_stats.reconnect_count,
                "is_healthy": info.connection.is_healthy,
                **registry_stats
            })
        
        return result
```

### Acceptance Criteria

1. Creates new connections when pending markets exceed threshold
2. Respects 500 market limit per connection
3. Tracks pollution ratio correctly
4. Initiates recycling when pollution > 30%
5. Graceful shutdown stops all connections
6. Stats aggregation works correctly
7. Force subscribe bypasses queue and creates immediate connection

---

## Component 6: Async Message Router

**File**: `src/router.py`

**Purpose**: Routes messages from connections to worker processes via multiprocessing queues.

### Requirements

1. Receive messages from all connections via callback
2. Buffer in async queue for backpressure handling
3. Route to worker queues based on asset_id hash (consistent hashing)
4. Handle queue full conditions gracefully
5. Track routing metrics

### Implementation Specification

```python
"""
Async Message Router - Bridges async domain to multiprocessing workers.

Architecture:
- Receives ParsedMessage from WebSocket connections
- Buffers in bounded asyncio.Queue for backpressure
- Routes to worker processes via multiprocessing.Queue
- Uses consistent hashing for worker assignment

Key patterns:
- Bounded queues prevent memory exhaustion
- Non-blocking puts with overflow handling
- Consistent hashing ensures same asset always goes to same worker
"""

import asyncio
from multiprocessing import Queue as MPQueue
from queue import Full
from typing import Dict, List, Callable
import hashlib
import logging
import time
from dataclasses import dataclass, field

from .protocol import ParsedMessage, EventType

logger = logging.getLogger(__name__)

# Configuration
ASYNC_QUEUE_SIZE = 10_000  # Max pending in async queue
WORKER_QUEUE_SIZE = 5_000  # Max pending per worker
BATCH_SIZE = 100  # Messages to batch before routing
BATCH_TIMEOUT = 0.01  # 10ms max wait for batch
PUT_TIMEOUT = 0.001  # 1ms timeout for queue put


@dataclass(slots=True)
class RouterStats:
    """Routing performance metrics."""
    messages_routed: int = 0
    messages_dropped: int = 0
    batches_sent: int = 0
    queue_full_events: int = 0
    routing_errors: int = 0
    total_latency_ms: float = 0.0
    
    @property
    def avg_latency_ms(self) -> float:
        if self.messages_routed > 0:
            return self.total_latency_ms / self.messages_routed
        return 0.0


def _hash_to_worker(asset_id: str, num_workers: int) -> int:
    """
    Consistent hash of asset_id to worker index.
    
    Uses MD5 for speed (not security-sensitive).
    """
    hash_bytes = hashlib.md5(asset_id.encode()).digest()
    hash_int = int.from_bytes(hash_bytes[:8], 'little')
    return hash_int % num_workers


class MessageRouter:
    """
    Routes messages from async domain to worker processes.
    
    Usage:
        router = MessageRouter(num_workers=4)
        queues = router.get_worker_queues()  # Pass to worker processes
        await router.start()
        
        # In connection callback:
        await router.route_message(connection_id, message)
    """
    
    __slots__ = (
        '_num_workers', '_worker_queues', '_async_queue',
        '_routing_task', '_running', '_stats', '_asset_worker_cache'
    )
    
    def __init__(self, num_workers: int) -> None:
        """
        Initialize router.
        
        Args:
            num_workers: Number of worker processes to route to
        """
        self._num_workers = num_workers
        self._worker_queues: List[MPQueue] = [
            MPQueue(maxsize=WORKER_QUEUE_SIZE)
            for _ in range(num_workers)
        ]
        self._async_queue: asyncio.Queue[tuple[str, ParsedMessage, float]] = asyncio.Queue(
            maxsize=ASYNC_QUEUE_SIZE
        )
        self._routing_task: asyncio.Task | None = None
        self._running = False
        self._stats = RouterStats()
        
        # Cache asset_id -> worker_index mapping
        self._asset_worker_cache: Dict[str, int] = {}
    
    @property
    def stats(self) -> RouterStats:
        return self._stats
    
    def get_worker_queues(self) -> List[MPQueue]:
        """Get queues to pass to worker processes."""
        return self._worker_queues
    
    def get_worker_for_asset(self, asset_id: str) -> int:
        """Get worker index for an asset (cached)."""
        if asset_id not in self._asset_worker_cache:
            self._asset_worker_cache[asset_id] = _hash_to_worker(
                asset_id, self._num_workers
            )
        return self._asset_worker_cache[asset_id]
    
    async def start(self) -> None:
        """Start routing task."""
        self._running = True
        self._routing_task = asyncio.create_task(
            self._routing_loop(),
            name="message-router"
        )
        logger.info(f"Message router started with {self._num_workers} workers")
    
    async def stop(self) -> None:
        """Stop routing and cleanup."""
        self._running = False
        
        if self._routing_task:
            self._routing_task.cancel()
            try:
                await self._routing_task
            except asyncio.CancelledError:
                pass
        
        # Signal workers to stop (send None sentinel)
        for q in self._worker_queues:
            try:
                q.put_nowait(None)
            except Full:
                pass
        
        logger.info(
            f"Message router stopped. Stats: "
            f"routed={self._stats.messages_routed}, "
            f"dropped={self._stats.messages_dropped}, "
            f"avg_latency={self._stats.avg_latency_ms:.2f}ms"
        )
    
    async def route_message(
        self, 
        connection_id: str, 
        message: ParsedMessage
    ) -> bool:
        """
        Route a message to appropriate worker.
        
        Called from connection callbacks.
        Returns True if queued successfully.
        """
        try:
            # Add receive timestamp for latency tracking
            self._async_queue.put_nowait((connection_id, message, time.monotonic()))
            return True
        except asyncio.QueueFull:
            self._stats.messages_dropped += 1
            logger.warning("Async queue full, message dropped")
            return False
    
    async def _routing_loop(self) -> None:
        """
        Main routing loop - batches messages and routes to workers.
        """
        batch: List[tuple[str, ParsedMessage, float]] = []
        
        while self._running:
            try:
                # Collect batch
                batch.clear()
                deadline = time.monotonic() + BATCH_TIMEOUT
                
                while len(batch) < BATCH_SIZE:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    
                    try:
                        item = await asyncio.wait_for(
                            self._async_queue.get(),
                            timeout=remaining
                        )
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break
                
                if batch:
                    await self._route_batch(batch)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Routing loop error: {e}", exc_info=True)
                self._stats.routing_errors += 1
    
    async def _route_batch(
        self, 
        batch: List[tuple[str, ParsedMessage, float]]
    ) -> None:
        """
        Route batch of messages to workers.
        
        Groups by worker for efficiency.
        """
        # Group by worker
        worker_batches: Dict[int, List[tuple[ParsedMessage, float]]] = {
            i: [] for i in range(self._num_workers)
        }
        
        for connection_id, message, receive_ts in batch:
            # Determine routing key
            asset_id = message.asset_id
            
            # For price_change events with multiple assets, route to all relevant workers
            if message.event_type == EventType.PRICE_CHANGE and message.price_change:
                seen_workers = set()
                for change in message.price_change.changes:
                    worker_idx = self.get_worker_for_asset(change.asset_id)
                    if worker_idx not in seen_workers:
                        worker_batches[worker_idx].append((message, receive_ts))
                        seen_workers.add(worker_idx)
            else:
                worker_idx = self.get_worker_for_asset(asset_id)
                worker_batches[worker_idx].append((message, receive_ts))
        
        # Send to workers
        now = time.monotonic()
        
        for worker_idx, messages in worker_batches.items():
            if not messages:
                continue
            
            queue = self._worker_queues[worker_idx]
            
            for message, receive_ts in messages:
                try:
                    # Serialize message for multiprocessing
                    # Using pickle implicitly via Queue
                    queue.put((message, receive_ts), timeout=PUT_TIMEOUT)
                    
                    self._stats.messages_routed += 1
                    self._stats.total_latency_ms += (now - receive_ts) * 1000
                    
                except Full:
                    self._stats.queue_full_events += 1
                    self._stats.messages_dropped += 1
        
        self._stats.batches_sent += 1
    
    def get_queue_depths(self) -> Dict[str, int]:
        """Get current queue depths for monitoring."""
        return {
            "async_queue": self._async_queue.qsize(),
            **{
                f"worker_{i}": q.qsize() 
                for i, q in enumerate(self._worker_queues)
            }
        }
```

### Acceptance Criteria

1. Messages routed to correct worker based on asset_id hash
2. Same asset_id always routes to same worker (consistency)
3. Backpressure handled gracefully (drops with logging)
4. Multi-asset price_change events fan out correctly
5. Latency tracking accurate to within 1ms
6. Stop sends sentinel to all workers
7. Queue depths reportable for monitoring

---

## Component 7: Worker Process Manager

**File**: `src/worker.py`

**Purpose**: Manages worker processes that maintain orderbook state.

### Requirements

1. Spawn worker processes with dedicated queues
2. Workers maintain OrderbookStore from Component 2
3. Process messages and update state
4. Support custom handlers for state changes
5. Graceful shutdown with state preservation option
6. Health monitoring via heartbeat

### Implementation Specification

```python
"""
Worker Process Manager - Manages CPU-intensive orderbook workers.

Each worker:
- Runs in separate OS process (bypasses GIL)
- Owns subset of orderbooks (determined by consistent hash)
- Processes messages from dedicated queue
- Can emit state change events
"""

import multiprocessing as mp
from multiprocessing import Process, Queue as MPQueue, Event as MPEvent
from multiprocessing.synchronize import Event
from queue import Empty
import signal
import logging
import time
from typing import List, Callable, Any, Optional
from dataclasses import dataclass, field

from .protocol import ParsedMessage, EventType, PriceChange
from .orderbook import OrderbookStore, OrderbookState

# Configure multiprocessing logging
logger = logging.getLogger(__name__)

# Configuration
QUEUE_TIMEOUT = 0.1  # 100ms timeout for queue reads
HEARTBEAT_INTERVAL = 5.0  # Seconds between heartbeats
STATS_INTERVAL = 30.0  # Seconds between stats reports


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


def _worker_process(
    worker_id: int,
    input_queue: MPQueue,
    stats_queue: MPQueue,
    shutdown_event: Event,
    handler_factory: Optional[Callable[[], Any]] = None
) -> None:
    """
    Worker process entry point.
    
    Runs in separate process - must be pickleable.
    """
    # Set up signal handling
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Parent handles SIGINT
    
    # Initialize state
    store = OrderbookStore()
    stats = WorkerStats()
    handler = handler_factory() if handler_factory else None
    
    last_heartbeat = time.monotonic()
    last_stats_report = time.monotonic()
    
    logger.info(f"Worker {worker_id} started")
    
    try:
        while not shutdown_event.is_set():
            try:
                # Get message with timeout
                item = input_queue.get(timeout=QUEUE_TIMEOUT)
                
                if item is None:
                    # Shutdown sentinel
                    break
                
                message, receive_ts = item
                process_start = time.monotonic()
                
                # Process message
                _process_message(store, message, stats, handler)
                
                # Track timing
                process_time = (time.monotonic() - process_start) * 1000
                stats.processing_time_ms += process_time
                stats.last_message_ts = time.monotonic()
                
            except Empty:
                pass
            
            # Periodic heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                stats.orderbook_count = len(store)
                stats.memory_usage_bytes = store.memory_usage()
                last_heartbeat = now
            
            # Periodic stats report
            if now - last_stats_report >= STATS_INTERVAL:
                try:
                    stats_queue.put_nowait((worker_id, stats))
                except:
                    pass
                last_stats_report = now
                
    except Exception as e:
        logger.error(f"Worker {worker_id} error: {e}", exc_info=True)
    finally:
        logger.info(
            f"Worker {worker_id} stopping. "
            f"Processed {stats.messages_processed} messages, "
            f"managing {len(store)} orderbooks"
        )


def _process_message(
    store: OrderbookStore,
    message: ParsedMessage,
    stats: WorkerStats,
    handler: Optional[Any]
) -> None:
    """
    Process single message and update state.
    """
    stats.messages_processed += 1
    
    if message.event_type == EventType.BOOK and message.book:
        book = message.book
        orderbook = store.get_or_create(book.asset_id)
        orderbook.apply_snapshot(book)
        store.register_market(book.asset_id, book.market)
        stats.snapshots_received += 1
        stats.updates_applied += 1
        
        if handler and hasattr(handler, 'on_snapshot'):
            handler.on_snapshot(book.asset_id, orderbook)
            
    elif message.event_type == EventType.PRICE_CHANGE and message.price_change:
        for change in message.price_change.changes:
            orderbook = store.get(change.asset_id)
            if orderbook:
                orderbook.apply_price_change(change)
                stats.updates_applied += 1
                
                if handler and hasattr(handler, 'on_price_change'):
                    handler.on_price_change(change.asset_id, orderbook, change)
                    
    elif message.event_type == EventType.LAST_TRADE_PRICE and message.last_trade:
        trade = message.last_trade
        orderbook = store.get(trade.asset_id)
        
        if handler and hasattr(handler, 'on_trade'):
            handler.on_trade(trade.asset_id, orderbook, trade)


class WorkerManager:
    """
    Manages pool of worker processes.
    
    Usage:
        manager = WorkerManager(num_workers=4)
        queues = manager.get_input_queues()  # Pass to router
        manager.start()
        # ... run ...
        manager.stop()
    """
    
    __slots__ = (
        '_num_workers', '_processes', '_input_queues', '_stats_queue',
        '_shutdown_event', '_handler_factory', '_running'
    )
    
    def __init__(
        self,
        num_workers: int,
        handler_factory: Optional[Callable[[], Any]] = None
    ) -> None:
        """
        Initialize manager.
        
        Args:
            num_workers: Number of worker processes
            handler_factory: Optional factory for per-worker handlers
                            Must be pickleable (function, not lambda)
        """
        self._num_workers = num_workers
        self._handler_factory = handler_factory
        self._processes: List[Process] = []
        self._input_queues: List[MPQueue] = []
        self._stats_queue: MPQueue = MPQueue()
        self._shutdown_event: Event = MPEvent()
        self._running = False
    
    def get_input_queues(self) -> List[MPQueue]:
        """Get input queues for workers (create if needed)."""
        if not self._input_queues:
            self._input_queues = [
                MPQueue(maxsize=5000)
                for _ in range(self._num_workers)
            ]
        return self._input_queues
    
    def start(self) -> None:
        """Start all worker processes."""
        if self._running:
            return
        
        self._shutdown_event.clear()
        queues = self.get_input_queues()
        
        for i in range(self._num_workers):
            p = Process(
                target=_worker_process,
                args=(
                    i,
                    queues[i],
                    self._stats_queue,
                    self._shutdown_event,
                    self._handler_factory
                ),
                name=f"orderbook-worker-{i}",
                daemon=True
            )
            p.start()
            self._processes.append(p)
        
        self._running = True
        logger.info(f"Started {self._num_workers} worker processes")
    
    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop all workers gracefully.
        
        Args:
            timeout: Max seconds to wait for workers
        """
        if not self._running:
            return
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Send sentinel values
        for q in self._input_queues:
            try:
                q.put_nowait(None)
            except:
                pass
        
        # Wait for processes
        deadline = time.monotonic() + timeout
        for p in self._processes:
            remaining = max(0, deadline - time.monotonic())
            p.join(timeout=remaining)
            
            if p.is_alive():
                logger.warning(f"Force terminating {p.name}")
                p.terminate()
                p.join(timeout=1.0)
        
        self._processes.clear()
        self._running = False
        logger.info("All worker processes stopped")
    
    def get_stats(self) -> dict[int, WorkerStats]:
        """
        Collect latest stats from all workers.
        
        Non-blocking - returns whatever is available.
        """
        stats = {}
        
        while True:
            try:
                worker_id, worker_stats = self._stats_queue.get_nowait()
                stats[worker_id] = worker_stats
            except Empty:
                break
        
        return stats
    
    def is_healthy(self) -> bool:
        """Check if all workers are alive."""
        return all(p.is_alive() for p in self._processes)
    
    def get_alive_count(self) -> int:
        """Count of alive worker processes."""
        return sum(1 for p in self._processes if p.is_alive())
```

### Acceptance Criteria

1. Worker processes start and run independently
2. Messages processed and orderbook state updated correctly
3. Custom handlers receive callbacks on state changes
4. Graceful shutdown within timeout
5. Stats collected via stats_queue
6. Health check detects dead workers
7. Memory usage per worker < 100MB for 1000 orderbooks

---

## Component 8: Market Lifecycle Controller

**File**: `src/lifecycle.py`

**Purpose**: Manages market discovery, expiration detection, and lifecycle events.

### Requirements

1. Poll for new markets from Polymarket API (Gamma)
2. Track market expiration times
3. Emit events for market lifecycle changes
4. Clean up expired markets after grace period

### Implementation Specification

```python
"""
Market Lifecycle Controller - Manages market discovery and expiration.

Responsibilities:
- Discover new markets via Polymarket Gamma API
- Track expiration times
- Detect and process expirations
- Clean up stale data
"""

import asyncio
import aiohttp
from typing import Set, Dict, List, Callable, Awaitable, Optional
from dataclasses import dataclass
import logging
import time

from .registry import MarketRegistry, MarketStatus

logger = logging.getLogger(__name__)

# Configuration
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DISCOVERY_INTERVAL = 60.0  # Check for new markets every minute
EXPIRATION_CHECK_INTERVAL = 30.0  # Check expirations every 30s
CLEANUP_DELAY = 3600.0  # Keep expired markets for 1 hour before cleanup
MAX_MARKETS_PER_REQUEST = 100

# Type aliases
LifecycleCallback = Callable[[str, str], Awaitable[None]]  # (event, asset_id)


@dataclass(slots=True)
class MarketInfo:
    """Market metadata from Gamma API."""
    condition_id: str
    question: str
    outcomes: list[str]
    tokens: list[dict]  # [{token_id, outcome}]
    end_date_iso: str
    end_timestamp: int  # Unix ms
    active: bool
    closed: bool


async def fetch_active_markets(
    session: aiohttp.ClientSession,
    limit: int = 100,
    offset: int = 0
) -> list[MarketInfo]:
    """
    Fetch active markets from Gamma API.
    
    Returns list of MarketInfo for active, non-closed markets.
    """
    url = f"{GAMMA_API_BASE}/markets"
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false"
    }
    
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning(f"Gamma API returned {resp.status}")
                return []
            
            data = await resp.json()
            
            markets = []
            for item in data:
                tokens = item.get("tokens", [])
                if not tokens:
                    continue
                
                # Parse end date
                end_date = item.get("endDate", "")
                try:
                    # ISO format: "2024-12-31T23:59:59Z"
                    from datetime import datetime
                    dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    end_ts = int(dt.timestamp() * 1000)
                except:
                    end_ts = 0
                
                markets.append(MarketInfo(
                    condition_id=item.get("conditionId", ""),
                    question=item.get("question", ""),
                    outcomes=item.get("outcomes", []),
                    tokens=tokens,
                    end_date_iso=end_date,
                    end_timestamp=end_ts,
                    active=item.get("active", False),
                    closed=item.get("closed", False)
                ))
            
            return markets
            
    except Exception as e:
        logger.error(f"Error fetching markets: {e}")
        return []


class LifecycleController:
    """
    Controls market lifecycle.
    
    Discovers new markets, tracks expirations, triggers cleanup.
    """
    
    __slots__ = (
        '_registry', '_session', '_running',
        '_discovery_task', '_expiration_task', '_cleanup_task',
        '_known_conditions', '_on_new_market', '_on_market_expired'
    )
    
    def __init__(
        self,
        registry: MarketRegistry,
        on_new_market: Optional[LifecycleCallback] = None,
        on_market_expired: Optional[LifecycleCallback] = None
    ) -> None:
        """
        Initialize controller.
        
        Args:
            registry: Market registry
            on_new_market: Callback for new market discovery
            on_market_expired: Callback for market expiration
        """
        self._registry = registry
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._discovery_task: Optional[asyncio.Task] = None
        self._expiration_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._known_conditions: Set[str] = set()
        self._on_new_market = on_new_market
        self._on_market_expired = on_market_expired
    
    async def start(self) -> None:
        """Start lifecycle management."""
        self._session = aiohttp.ClientSession()
        self._running = True
        
        # Initial market discovery
        await self._discover_markets()
        
        # Start background tasks
        self._discovery_task = asyncio.create_task(
            self._discovery_loop(),
            name="market-discovery"
        )
        self._expiration_task = asyncio.create_task(
            self._expiration_loop(),
            name="expiration-checker"
        )
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="cleanup-task"
        )
        
        logger.info("Lifecycle controller started")
    
    async def stop(self) -> None:
        """Stop lifecycle management."""
        self._running = False
        
        for task in [self._discovery_task, self._expiration_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        if self._session:
            await self._session.close()
        
        logger.info("Lifecycle controller stopped")
    
    async def _discovery_loop(self) -> None:
        """Periodically discover new markets."""
        while self._running:
            try:
                await asyncio.sleep(DISCOVERY_INTERVAL)
                await self._discover_markets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Discovery loop error: {e}", exc_info=True)
    
    async def _discover_markets(self) -> None:
        """Fetch and register new markets."""
        if not self._session:
            return
        
        offset = 0
        new_count = 0
        
        while True:
            markets = await fetch_active_markets(
                self._session,
                limit=MAX_MARKETS_PER_REQUEST,
                offset=offset
            )
            
            if not markets:
                break
            
            for market in markets:
                if market.condition_id in self._known_conditions:
                    continue
                
                self._known_conditions.add(market.condition_id)
                
                # Register each token (Yes/No outcomes)
                for token in market.tokens:
                    token_id = token.get("token_id", "")
                    if not token_id:
                        continue
                    
                    added = await self._registry.add_market(
                        asset_id=token_id,
                        condition_id=market.condition_id,
                        expiration_ts=market.end_timestamp
                    )
                    
                    if added:
                        new_count += 1
                        if self._on_new_market:
                            try:
                                await self._on_new_market("new_market", token_id)
                            except Exception as e:
                                logger.error(f"Callback error: {e}")
            
            offset += len(markets)
            
            if len(markets) < MAX_MARKETS_PER_REQUEST:
                break
        
        if new_count > 0:
            logger.info(f"Discovered {new_count} new market tokens")
    
    async def _expiration_loop(self) -> None:
        """Check for expired markets."""
        while self._running:
            try:
                await asyncio.sleep(EXPIRATION_CHECK_INTERVAL)
                await self._check_expirations()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Expiration loop error: {e}", exc_info=True)
    
    async def _check_expirations(self) -> None:
        """Mark expired markets."""
        now = int(time.time() * 1000)  # Current time in ms
        
        expiring = self._registry.get_expiring_before(now)
        
        if not expiring:
            return
        
        # Filter to only subscribed markets
        to_expire = [
            asset_id for asset_id in expiring
            if (entry := self._registry.get(asset_id)) 
            and entry.status == MarketStatus.SUBSCRIBED
        ]
        
        if to_expire:
            count = await self._registry.mark_expired(to_expire)
            logger.info(f"Marked {count} markets as expired")
            
            if self._on_market_expired:
                for asset_id in to_expire:
                    try:
                        await self._on_market_expired("market_expired", asset_id)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
    
    async def _cleanup_loop(self) -> None:
        """Clean up old expired markets."""
        while self._running:
            try:
                await asyncio.sleep(CLEANUP_DELAY)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}", exc_info=True)
    
    async def _cleanup_expired(self) -> None:
        """Remove long-expired markets from registry."""
        expired = self._registry.get_by_status(MarketStatus.EXPIRED)
        
        cleanup_count = 0
        for asset_id in expired:
            entry = self._registry.get(asset_id)
            if not entry:
                continue
            
            # Check if expired long enough
            age = time.monotonic() - entry.subscribed_at if entry.subscribed_at else 0
            if age > CLEANUP_DELAY:
                await self._registry.remove_market(asset_id)
                cleanup_count += 1
        
        if cleanup_count > 0:
            logger.info(f"Cleaned up {cleanup_count} expired markets")
    
    async def add_market_manually(
        self,
        asset_id: str,
        condition_id: str,
        expiration_ts: int = 0
    ) -> bool:
        """
        Manually add a market (bypasses discovery).
        
        Useful for specific markets you want to track.
        """
        return await self._registry.add_market(
            asset_id=asset_id,
            condition_id=condition_id,
            expiration_ts=expiration_ts
        )
```

### Acceptance Criteria

1. New markets discovered from Gamma API
2. All tokens (Yes/No) registered for each market
3. Expiration detection triggers within 30 seconds of expiry
4. Callbacks invoked for lifecycle events
5. Cleanup removes markets after grace period
6. Manual market addition works
7. API errors handled gracefully (retry, logging)

---

## Component 9: Connection Recycler

**File**: `src/recycler.py`

**Purpose**: Handles zero-downtime connection recycling.

### Requirements

1. Monitor connections for recycling triggers
2. Orchestrate seamless migration of active markets
3. Ensure no message loss during transition
4. Track recycling metrics

### Implementation Specification

```python
"""
Connection Recycler - Zero-downtime connection migration.

Recycling flow:
1. Detect trigger (pollution ratio, age, health)
2. Mark old connection as draining
3. Create new connection with active markets
4. Wait for new connection to stabilize
5. Atomically update registry mappings
6. Close old connection

Critical: Messages continue flowing during transition.
Both connections receive messages until swap is complete.
"""

import asyncio
from typing import Dict, Set, Optional, Callable, Awaitable
from dataclasses import dataclass, field
import logging
import time

from .registry import MarketRegistry, MarketStatus
from .pool import ConnectionPool
from .connection import WebSocketConnection, ConnectionStatus

logger = logging.getLogger(__name__)

# Configuration
POLLUTION_THRESHOLD = 0.30  # 30% expired triggers recycling
AGE_THRESHOLD = 86400.0  # 24 hours max age
HEALTH_CHECK_INTERVAL = 60.0  # Check health every minute
STABILIZATION_DELAY = 3.0  # Wait for new connection to stabilize
MAX_CONCURRENT_RECYCLES = 2  # Limit concurrent recycling operations


@dataclass(slots=True)
class RecycleStats:
    """Recycling performance metrics."""
    recycles_initiated: int = 0
    recycles_completed: int = 0
    recycles_failed: int = 0
    markets_migrated: int = 0
    total_downtime_ms: float = 0.0
    
    @property
    def success_rate(self) -> float:
        if self.recycles_initiated > 0:
            return self.recycles_completed / self.recycles_initiated
        return 1.0


class ConnectionRecycler:
    """
    Manages connection recycling for the pool.
    
    Monitors connections and triggers recycling when needed.
    Ensures zero message loss during migration.
    """
    
    __slots__ = (
        '_registry', '_pool', '_running', '_monitor_task',
        '_active_recycles', '_stats', '_recycle_semaphore'
    )
    
    def __init__(
        self,
        registry: MarketRegistry,
        pool: ConnectionPool
    ) -> None:
        """
        Initialize recycler.
        
        Args:
            registry: Market registry
            pool: Connection pool to manage
        """
        self._registry = registry
        self._pool = pool
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._active_recycles: Set[str] = set()  # Connection IDs being recycled
        self._stats = RecycleStats()
        self._recycle_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RECYCLES)
    
    @property
    def stats(self) -> RecycleStats:
        return self._stats
    
    async def start(self) -> None:
        """Start recycler monitoring."""
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="recycler-monitor"
        )
        logger.info("Connection recycler started")
    
    async def stop(self) -> None:
        """Stop recycler."""
        self._running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info(
            f"Connection recycler stopped. Stats: "
            f"initiated={self._stats.recycles_initiated}, "
            f"completed={self._stats.recycles_completed}, "
            f"failed={self._stats.recycles_failed}"
        )
    
    async def _monitor_loop(self) -> None:
        """Monitor connections for recycling needs."""
        while self._running:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                await self._check_all_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}", exc_info=True)
    
    async def _check_all_connections(self) -> None:
        """Check all connections for recycling triggers."""
        connection_stats = self._pool.get_connection_stats()
        
        for stats in connection_stats:
            connection_id = stats["connection_id"]
            
            # Skip if already recycling
            if connection_id in self._active_recycles:
                continue
            
            # Skip if draining
            if stats.get("is_draining"):
                continue
            
            # Check triggers
            should_recycle = False
            reason = ""
            
            if stats["pollution_ratio"] >= POLLUTION_THRESHOLD:
                should_recycle = True
                reason = f"pollution={stats['pollution_ratio']:.1%}"
            elif stats.get("age_seconds", 0) >= AGE_THRESHOLD:
                should_recycle = True
                reason = f"age={stats['age_seconds']/3600:.1f}h"
            elif not stats.get("is_healthy", True):
                should_recycle = True
                reason = "unhealthy"
            
            if should_recycle:
                logger.info(
                    f"Triggering recycle for {connection_id}: {reason}"
                )
                asyncio.create_task(
                    self._recycle_connection(connection_id),
                    name=f"recycle-{connection_id}"
                )
    
    async def _recycle_connection(self, connection_id: str) -> bool:
        """
        Perform connection recycling.
        
        Returns True if successful.
        """
        async with self._recycle_semaphore:
            self._active_recycles.add(connection_id)
            self._stats.recycles_initiated += 1
            
            start_time = time.monotonic()
            
            try:
                # Get active markets for this connection
                active_assets = list(
                    self._registry.get_active_by_connection(connection_id)
                )
                
                if not active_assets:
                    logger.info(
                        f"Connection {connection_id} has no active markets, "
                        "removing without replacement"
                    )
                    await self._pool._remove_connection(connection_id)
                    self._stats.recycles_completed += 1
                    return True
                
                logger.info(
                    f"Recycling {connection_id}: migrating {len(active_assets)} markets"
                )
                
                # Create new connection via pool
                new_connection_id = await self._pool.force_subscribe(active_assets)
                
                # Wait for stabilization (new connection receiving messages)
                await asyncio.sleep(STABILIZATION_DELAY)
                
                # Verify new connection is healthy
                new_stats = self._pool.get_connection_stats()
                new_conn = next(
                    (s for s in new_stats if s["connection_id"] == new_connection_id),
                    None
                )
                
                if not new_conn or not new_conn.get("is_healthy", False):
                    logger.error(
                        f"New connection {new_connection_id} not healthy, "
                        "aborting recycle"
                    )
                    self._stats.recycles_failed += 1
                    return False
                
                # Atomic registry update
                migrated = await self._registry.reassign_connection(
                    active_assets,
                    connection_id,
                    new_connection_id
                )
                
                # Remove old connection
                await self._pool._remove_connection(connection_id)
                
                duration_ms = (time.monotonic() - start_time) * 1000
                self._stats.recycles_completed += 1
                self._stats.markets_migrated += migrated
                self._stats.total_downtime_ms += duration_ms
                
                logger.info(
                    f"Recycle complete: {connection_id} -> {new_connection_id}, "
                    f"migrated {migrated} markets in {duration_ms:.0f}ms"
                )
                
                return True
                
            except Exception as e:
                logger.error(
                    f"Recycle failed for {connection_id}: {e}",
                    exc_info=True
                )
                self._stats.recycles_failed += 1
                return False
                
            finally:
                self._active_recycles.discard(connection_id)
    
    async def force_recycle(self, connection_id: str) -> bool:
        """
        Force immediate recycling of a connection.
        
        Useful for manual intervention.
        """
        return await self._recycle_connection(connection_id)
    
    def get_active_recycles(self) -> Set[str]:
        """Get connection IDs currently being recycled."""
        return self._active_recycles.copy()
```

### Acceptance Criteria

1. Recycling triggers on pollution > 30%
2. Recycling triggers on age > 24 hours
3. Recycling triggers on unhealthy connection
4. No message loss during recycling (both connections receive during transition)
5. Registry atomically updated
6. Concurrent recycles limited to MAX_CONCURRENT_RECYCLES
7. Stats accurately track recycles and migrations
8. Force recycle works for manual intervention

---

## Component 10: Main Application Orchestrator

**File**: `src/app.py`

**Purpose**: Ties all components together into running application.

### Requirements

1. Initialize and wire all components
2. Provide clean startup/shutdown sequence
3. Expose health check and stats endpoints
4. Handle signals (SIGINT, SIGTERM) gracefully

### Implementation Specification

```python
"""
Main Application Orchestrator - Ties everything together.

Startup sequence:
1. Initialize registry
2. Start worker processes
3. Create message router
4. Start connection pool
5. Start lifecycle controller
6. Start recycler

Shutdown sequence (reverse):
1. Stop lifecycle controller
2. Stop recycler
3. Stop connection pool
4. Stop router
5. Stop workers
6. Cleanup
"""

import asyncio
import signal
from typing import Optional, Callable, Any, Dict
import logging
import sys

from .protocol import ParsedMessage
from .registry import MarketRegistry
from .pool import ConnectionPool
from .router import MessageRouter
from .worker import WorkerManager
from .lifecycle import LifecycleController
from .recycler import ConnectionRecycler

logger = logging.getLogger(__name__)


class Application:
    """
    Main application orchestrator.
    
    Usage:
        app = Application(num_workers=4)
        await app.start()
        # ... run until shutdown signal ...
        await app.stop()
    
    Or use run() for automatic signal handling:
        app = Application(num_workers=4)
        await app.run()
    """
    
    __slots__ = (
        '_num_workers', '_handler_factory',
        '_registry', '_pool', '_router', '_workers',
        '_lifecycle', '_recycler', '_running', '_shutdown_event'
    )
    
    def __init__(
        self,
        num_workers: int = 4,
        handler_factory: Optional[Callable[[], Any]] = None
    ) -> None:
        """
        Initialize application.
        
        Args:
            num_workers: Number of orderbook worker processes
            handler_factory: Optional factory for per-worker handlers
        """
        self._num_workers = num_workers
        self._handler_factory = handler_factory
        
        # Components (initialized on start)
        self._registry: Optional[MarketRegistry] = None
        self._pool: Optional[ConnectionPool] = None
        self._router: Optional[MessageRouter] = None
        self._workers: Optional[WorkerManager] = None
        self._lifecycle: Optional[LifecycleController] = None
        self._recycler: Optional[ConnectionRecycler] = None
        
        self._running = False
        self._shutdown_event = asyncio.Event()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    async def start(self) -> None:
        """Start all components in correct order."""
        if self._running:
            return
        
        logger.info("Starting Polymarket WebSocket application...")
        
        # 1. Initialize registry
        self._registry = MarketRegistry()
        logger.info("Registry initialized")
        
        # 2. Start workers
        self._workers = WorkerManager(
            num_workers=self._num_workers,
            handler_factory=self._handler_factory
        )
        self._workers.start()
        logger.info(f"Started {self._num_workers} worker processes")
        
        # 3. Create router (using worker queues)
        self._router = MessageRouter(num_workers=self._num_workers)
        # Connect router queues to workers
        worker_queues = self._workers.get_input_queues()
        self._router._worker_queues = worker_queues
        await self._router.start()
        logger.info("Message router started")
        
        # 4. Create message callback for pool
        async def on_message(connection_id: str, message: ParsedMessage) -> None:
            await self._router.route_message(connection_id, message)
        
        # 5. Start connection pool
        self._pool = ConnectionPool(
            registry=self._registry,
            message_callback=on_message
        )
        await self._pool.start()
        logger.info("Connection pool started")
        
        # 6. Start lifecycle controller
        self._lifecycle = LifecycleController(
            registry=self._registry,
            on_new_market=self._on_new_market,
            on_market_expired=self._on_market_expired
        )
        await self._lifecycle.start()
        logger.info("Lifecycle controller started")
        
        # 7. Start recycler
        self._recycler = ConnectionRecycler(
            registry=self._registry,
            pool=self._pool
        )
        await self._recycler.start()
        logger.info("Connection recycler started")
        
        self._running = True
        logger.info("Application started successfully")
    
    async def stop(self) -> None:
        """Stop all components in reverse order."""
        if not self._running:
            return
        
        logger.info("Stopping application...")
        
        # Reverse order shutdown
        if self._recycler:
            await self._recycler.stop()
        
        if self._lifecycle:
            await self._lifecycle.stop()
        
        if self._pool:
            await self._pool.stop()
        
        if self._router:
            await self._router.stop()
        
        if self._workers:
            self._workers.stop()
        
        self._running = False
        logger.info("Application stopped")
    
    async def run(self) -> None:
        """
        Run application with signal handling.
        
        Blocks until SIGINT or SIGTERM received.
        """
        # Set up signal handlers
        loop = asyncio.get_event_loop()
        
        def signal_handler():
            logger.info("Shutdown signal received")
            self._shutdown_event.set()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
        
        try:
            await self.start()
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        finally:
            await self.stop()
            
            # Remove signal handlers
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
    
    async def _on_new_market(self, event: str, asset_id: str) -> None:
        """Handle new market discovery."""
        logger.debug(f"New market discovered: {asset_id}")
        # Could trigger immediate subscription if desired
    
    async def _on_market_expired(self, event: str, asset_id: str) -> None:
        """Handle market expiration."""
        logger.debug(f"Market expired: {asset_id}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive application statistics."""
        stats = {
            "running": self._running,
            "registry": {},
            "pool": {},
            "router": {},
            "workers": {},
            "recycler": {}
        }
        
        if self._registry:
            stats["registry"] = {
                "total_markets": len(self._registry),
                "pending": self._registry.get_pending_count(),
                "subscribed": len(self._registry.get_by_status(
                    from .registry import MarketStatus
                    MarketStatus.SUBSCRIBED
                )),
                "expired": len(self._registry.get_by_status(
                    MarketStatus.EXPIRED
                ))
            }
        
        if self._pool:
            stats["pool"] = {
                "connection_count": self._pool.connection_count,
                "active_connections": self._pool.active_connection_count,
                "connections": self._pool.get_connection_stats()
            }
        
        if self._router:
            rs = self._router.stats
            stats["router"] = {
                "messages_routed": rs.messages_routed,
                "messages_dropped": rs.messages_dropped,
                "avg_latency_ms": rs.avg_latency_ms,
                "queue_depths": self._router.get_queue_depths()
            }
        
        if self._workers:
            stats["workers"] = {
                "alive_count": self._workers.get_alive_count(),
                "is_healthy": self._workers.is_healthy(),
                "worker_stats": self._workers.get_stats()
            }
        
        if self._recycler:
            rs = self._recycler.stats
            stats["recycler"] = {
                "recycles_completed": rs.recycles_completed,
                "recycles_failed": rs.recycles_failed,
                "markets_migrated": rs.markets_migrated,
                "active_recycles": list(self._recycler.get_active_recycles())
            }
        
        return stats
    
    def is_healthy(self) -> bool:
        """Check overall application health."""
        if not self._running:
            return False
        
        if self._workers and not self._workers.is_healthy():
            return False
        
        if self._pool and self._pool.active_connection_count == 0:
            # No active connections (might be during startup)
            pass
        
        return True


# Entry point
async def main():
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    
    app = Application(num_workers=4)
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
```

### Acceptance Criteria

1. Clean startup sequence with logging
2. Clean shutdown sequence (reverse order)
3. SIGINT/SIGTERM handled gracefully
4. Stats endpoint returns comprehensive data
5. Health check reflects actual system state
6. All components properly wired together
7. Application can run for extended periods without memory leaks

---

## Project Structure

```
polymarket_ws/
├── src/
│   ├── __init__.py
│   ├── protocol.py      # Component 1: Messages & Types
│   ├── orderbook.py     # Component 2: Orderbook State
│   ├── registry.py      # Component 3: Market Registry
│   ├── connection.py    # Component 4: WebSocket Connection
│   ├── pool.py          # Component 5: Connection Pool
│   ├── router.py        # Component 6: Message Router
│   ├── worker.py        # Component 7: Worker Manager
│   ├── lifecycle.py     # Component 8: Lifecycle Controller
│   ├── recycler.py      # Component 9: Connection Recycler
│   └── app.py           # Component 10: Orchestrator
├── tests/
│   ├── test_protocol.py
│   ├── test_orderbook.py
│   ├── test_registry.py
│   ├── test_connection.py
│   ├── test_pool.py
│   ├── test_router.py
│   ├── test_worker.py
│   └── test_integration.py
├── pyproject.toml
└── README.md
```

## Dependencies

```toml
[project]
name = "polymarket-ws"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "websockets>=12.0",
    "msgspec>=0.18.0",
    "sortedcontainers>=2.4.0",
    "aiohttp>=3.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-timeout>=2.2",
]
```

## Build Order Summary

| Order | Component | File | Dependencies |
|-------|-----------|------|--------------|
| 1 | Protocol Messages | protocol.py | None |
| 2 | Orderbook State | orderbook.py | protocol.py |
| 3 | Market Registry | registry.py | None |
| 4 | WebSocket Connection | connection.py | protocol.py |
| 5 | Connection Pool | pool.py | connection.py, registry.py |
| 6 | Message Router | router.py | protocol.py |
| 7 | Worker Manager | worker.py | protocol.py, orderbook.py |
| 8 | Lifecycle Controller | lifecycle.py | registry.py |
| 9 | Connection Recycler | recycler.py | registry.py, pool.py |
| 10 | Application | app.py | All above |

## Testing Strategy

Each component should be tested independently before integration:

1. **Unit tests**: Test each class/function in isolation
2. **Integration tests**: Test component pairs (e.g., Router + Worker)
3. **End-to-end tests**: Test full message flow with mock WebSocket
4. **Load tests**: Verify performance targets under load
5. **Chaos tests**: Verify resilience (kill workers, disconnect sockets)

---

*End of specification*
