"""
Protocol message definitions using msgspec for zero-copy parsing.

Key patterns:
- msgspec.Struct instead of dataclass (faster, less memory)
- __slots__ equivalent is automatic with Struct
- Frozen structs for immutability where appropriate
- Integer price representation: actual_price = price_int / 100
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Final

import msgspec

# Final indicates that it should not be reassigned, redefined or overridden
PRICE_SCALE: Final[int] = 1000
SIZE_SCALE: Final[int] = 100


class Side(IntEnum):
    """Order side as integer for fast comparison"""

    BUY = 0
    SELL = 1


class EventType(IntEnum):
    """Event type enumeration for switch-based dispatch"""

    BOOK = 0
    PRICE_CHANGE = 1
    LAST_TRADE_PRICE = 2
    TICK_SIZE_CHANGE = 3
    UNKNOWN = 255


EVENT_TYPE_MAP: Final[dict[str, EventType]] = {
    "book": EventType.BOOK,
    "price_change": EventType.PRICE_CHANGE,
    "last_trade_price": EventType.LAST_TRADE_PRICE,
    "tick_size_change": EventType.TICK_SIZE_CHANGE,
}

SIDE_MAP: Final[dict[str, Side]] = {
    "BUY": Side.BUY,
    "SELL": Side.SELL,
}


def scale_price(price: str) -> int:
    return int(float(price) * PRICE_SCALE)


def scale_size(size: str) -> int:
    return int(float(size) * SIZE_SCALE)


################
# BOOK MESSAGE
# ##############


class PriceLevel(msgspec.Struct, frozen=True, array_like=True):
    """
    Single price level in orderbook

    array_like=True encodes as JSON array [price, size] saving bytes
    frozen=True enables hashing and prevents mutation
    """

    price: int  # scaled integer
    size: int

    @classmethod
    def from_strings(cls, price: str, size: str) -> PriceLevel:
        """Parse from API string format"""
        return cls(
            price=scale_price(price),
            size=scale_size(size),
        )


class BookSnapshot(msgspec.Struct, frozen=True):
    """Full orderbook snapshot"""

    # asset_id: str
    # market: str
    # timestamp: int  # Unix ms as integer
    hash: str
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]


######################
# PRICE CHANGE MESSAGE
# ####################


class PriceChange(msgspec.Struct, frozen=True):
    """Single price change event"""

    asset_id: str
    price: int
    size: int
    side: Side
    hash: str
    best_bid: int
    best_ask: int

    @classmethod
    def from_strings(
        cls,
        asset_id: str,
        price: str,
        size: str,
        side: str,
        hash: str,
        best_bid: str,
        best_ask: str,
    ) -> PriceChange:
        return cls(
            asset_id=asset_id,
            price=scale_price(price),
            size=scale_size(size),
            side=SIDE_MAP[side],
            hash=hash,
            best_bid=scale_price(best_bid),
            best_ask=scale_price(best_ask),
        )


class PriceChangeEvent(msgspec.Struct):
    """Price change event with multiple changes"""

    market: str
    timestamp: int
    price_changes: tuple[PriceChange, ...]


class LastTradePrice(msgspec.Struct, frozen=True):
    """Last trade price event"""

    price: int
    size: int
    side: Side

    @classmethod
    def from_strings(
        cls,
        price: str,
        size: str,
        side: str,
    ) -> LastTradePrice:
        return cls(
            price=scale_price(price),
            size=scale_size(size),
            side=SIDE_MAP[side],
        )


class TickSizeChange(msgspec.Struct, frozen=True):
    """Tick size change event"""

    old_tick_size: float
    new_tick_size: float

    @classmethod
    def from_strings(
        cls,
        old_tick_size: str,
        new_tick_size: str,
    ) -> TickSizeChange:
        return cls(
            old_tick_size=float(old_tick_size),
            new_tick_size=float(new_tick_size),
        )


class ParsedMessage(msgspec.Struct):
    """
    Unified parsed message container

    Uses tagged union pattern - event_type determines which field is populated.
    Only one of book/price_change/last_trade will be non-none
    """

    event_type: EventType
    asset_id: str  # Primary routing key
    market: str
    raw_timestamp: int
    book: BookSnapshot | None = None
    price_change: PriceChange | None = None
    last_trade: LastTradePrice | None = None
    tick_size_change: TickSizeChange | None = None


@dataclass(slots=True)
class RawMessage:
    event_type: EventType
    market: str
    timestamp: int
    raw_data: dict[str, Any]
