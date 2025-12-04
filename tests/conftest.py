"""Shared pytest fixtures for all test modules."""

import pytest

from src.messages.protocol import (
    BookSnapshot,
    PriceChange,
    PriceLevel,
    Side,
)


# Sample asset and market IDs
@pytest.fixture
def sample_asset_id() -> str:
    return (
        "71321045679252212594626385532706912750332728571942532289631379312455583992563"
    )


@pytest.fixture
def sample_market() -> str:
    return "0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1"


# Sample price levels
@pytest.fixture
def sample_bid_levels() -> tuple[PriceLevel, ...]:
    return (
        PriceLevel(price=490, size=2000),  # 0.49 @ 20.00
        PriceLevel(price=480, size=3000),  # 0.48 @ 30.00
    )


@pytest.fixture
def sample_ask_levels() -> tuple[PriceLevel, ...]:
    return (
        PriceLevel(price=510, size=2500),  # 0.51 @ 25.00
        PriceLevel(price=520, size=1500),  # 0.52 @ 15.00
    )


# Sample book snapshot
@pytest.fixture
def sample_book_snapshot(
    sample_bid_levels: tuple[PriceLevel, ...],
    sample_ask_levels: tuple[PriceLevel, ...],
) -> BookSnapshot:
    return BookSnapshot(
        hash="0xabc123",
        bids=sample_bid_levels,
        asks=sample_ask_levels,
    )


# Sample price change
@pytest.fixture
def sample_price_change(sample_asset_id: str) -> PriceChange:
    return PriceChange(
        asset_id=sample_asset_id,
        price=500,
        size=1000,
        side=Side.BUY,
        hash="0xdef456",
        best_bid=500,
        best_ask=510,
    )
