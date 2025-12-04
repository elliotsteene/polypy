"""Tests for protocol message type conversions."""

import pytest

from src.messages.protocol import (
    LastTradePrice,
    PriceChange,
    PriceLevel,
    Side,
    scale_price,
    scale_size,
)


class TestScalingFunctions:
    """Test price and size scaling utilities."""

    @pytest.mark.parametrize(
        "price_str,expected",
        [
            ("0.50", 500),
            ("0.456", 456),
            ("1.0", 1000),
            ("0.001", 1),
            ("0", 0),
        ],
    )
    def test_scale_price(self, price_str: str, expected: int) -> None:
        # Arrange & Act
        result = scale_price(price_str)

        # Assert
        assert result == expected

    @pytest.mark.parametrize(
        "size_str,expected",
        [
            ("100", 10000),
            ("10.25", 1025),
            ("219.217767", 21921),  # Truncates decimals beyond 2 places
            ("0", 0),
        ],
    )
    def test_scale_size(self, size_str: str, expected: int) -> None:
        # Arrange & Act
        result = scale_size(size_str)

        # Assert
        assert result == expected


class TestPriceLevel:
    """Test PriceLevel struct and conversion."""

    def test_from_strings_valid(self) -> None:
        # Arrange
        price_str = "0.48"
        size_str = "30"

        # Act
        level = PriceLevel.from_strings(price_str, size_str)

        # Assert
        assert level.price == 480
        assert level.size == 3000

    def test_price_level_frozen(self) -> None:
        # Arrange
        level = PriceLevel(price=500, size=1000)

        # Act & Assert
        with pytest.raises(AttributeError):
            level.price = 600  # type: ignore


class TestPriceChange:
    """Test PriceChange struct and conversion."""

    def test_from_strings_buy_side(self) -> None:
        # Arrange
        asset_id = "12345"
        price = "0.5"
        size = "200"
        side = "BUY"
        hash_val = "0xabc"
        best_bid = "0.5"
        best_ask = "0.51"

        # Act
        change = PriceChange.from_strings(
            asset_id=asset_id,
            price=price,
            size=size,
            side=side,
            hash=hash_val,
            best_bid=best_bid,
            best_ask=best_ask,
        )

        # Assert
        assert change.asset_id == asset_id
        assert change.price == 500
        assert change.size == 20000
        assert change.side == Side.BUY
        assert change.hash == hash_val
        assert change.best_bid == 500
        assert change.best_ask == 510

    def test_from_strings_sell_side(self) -> None:
        # Arrange
        asset_id = "67890"
        side = "SELL"

        # Act
        change = PriceChange.from_strings(
            asset_id=asset_id,
            price="0.49",
            size="100",
            side=side,
            hash="0xdef",
            best_bid="0.48",
            best_ask="0.49",
        )

        # Assert
        assert change.side == Side.SELL
        assert change.price == 490
        assert change.size == 10000


class TestLastTradePrice:
    """Test LastTradePrice struct and conversion."""

    def test_from_strings_valid(self) -> None:
        # Arrange
        price = "0.456"
        size = "219.217767"
        side = "BUY"

        # Act
        trade = LastTradePrice.from_strings(price=price, size=size, side=side)

        # Assert
        assert trade.price == 456
        assert trade.size == 21921
        assert trade.side == Side.BUY
