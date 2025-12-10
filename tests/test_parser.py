import pytest

from src.messages.parser import MessageParser
from src.messages.protocol import (
    BookSnapshot,
    EventType,
    LastTradePrice,
    ParsedMessage,
    PriceChange,
    PriceLevel,
    Side,
    TickSizeChange,
)


@pytest.mark.parametrize(
    argnames=("bytes,expected"),
    argvalues=[
        (
            b'{"event_type": "book","asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422","market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af","bids": [{ "price": ".48", "size": "30" },{ "price": ".49", "size": "20" }],"asks": [{ "price": ".52", "size": "25" },{ "price": ".53", "size": "60" }],"timestamp": "123456789000","hash": "0x0...."}',
            [
                ParsedMessage(
                    event_type=EventType.BOOK,
                    market="0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
                    asset_id="65818619657568813474341868652308942079804919287380422192892211131408793125422",
                    raw_timestamp=123456789000,
                    book=BookSnapshot(
                        hash="0x0....",
                        bids=(
                            PriceLevel(
                                price=480,
                                size=3000,
                            ),
                            PriceLevel(
                                price=490,
                                size=2000,
                            ),
                        ),
                        asks=(
                            PriceLevel(
                                price=520,
                                size=2500,
                            ),
                            PriceLevel(
                                price=530,
                                size=6000,
                            ),
                        ),
                    ),
                )
            ],
        ),
        (
            b'{"market": "0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1","price_changes": [{"asset_id": "71321045679252212594626385532706912750332728571942532289631379312455583992563","price": "0.5","size": "200","side": "BUY","hash": "56621a121a47ed9333273e21c83b660cff37ae50","best_bid": "0.5","best_ask": "1"},{"asset_id": "52114319501245915516055106046884209969926127482827954674443846427813813222426","price": "0.5","size": "200","side": "SELL","hash": "1895759e4df7a796bf4f1c5a5950b748306923e2","best_bid": "0","best_ask": "0.5"}],"timestamp": "1757908892351","event_type": "price_change"}',
            [
                ParsedMessage(
                    event_type=EventType.PRICE_CHANGE,
                    market="0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1",
                    asset_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
                    raw_timestamp=1757908892351,
                    price_change=PriceChange(
                        asset_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
                        price=500,
                        size=20000,
                        side=Side.BUY,
                        hash="56621a121a47ed9333273e21c83b660cff37ae50",
                        best_bid=500,
                        best_ask=1000,
                    ),
                ),
                ParsedMessage(
                    event_type=EventType.PRICE_CHANGE,
                    market="0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1",
                    asset_id="52114319501245915516055106046884209969926127482827954674443846427813813222426",
                    raw_timestamp=1757908892351,
                    price_change=PriceChange(
                        asset_id="52114319501245915516055106046884209969926127482827954674443846427813813222426",
                        price=500,
                        size=20000,
                        side=Side.SELL,
                        hash="1895759e4df7a796bf4f1c5a5950b748306923e2",
                        best_bid=0,
                        best_ask=500,
                    ),
                ),
            ],
        ),
        (
            b'{"asset_id":"114122071509644379678018727908709560226618148003371446110114509806601493071694","event_type":"last_trade_price","fee_rate_bps":"0","market":"0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347","price":"0.456","side":"BUY","size":"219.217767","timestamp":"1750428146322"}',
            [
                ParsedMessage(
                    event_type=EventType.LAST_TRADE_PRICE,
                    market="0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347",
                    asset_id="114122071509644379678018727908709560226618148003371446110114509806601493071694",
                    raw_timestamp=1750428146322,
                    last_trade=LastTradePrice(
                        price=456,
                        size=21921,
                        side=Side.BUY,
                    ),
                )
            ],
        ),
        (
            b'{"event_type":"tick_size_change","market":"0xabc","timestamp":"1000", "old_tick_size":"0.001", "new_tick_size":"0.001","asset_id":"114122071509644379678018727908709560226618148003371446110114509806601493071694"}',
            [
                ParsedMessage(
                    event_type=EventType.TICK_SIZE_CHANGE,
                    market="0xabc",
                    asset_id="114122071509644379678018727908709560226618148003371446110114509806601493071694",
                    raw_timestamp=1000,
                    tick_size_change=TickSizeChange(
                        old_tick_size=0.001,
                        new_tick_size=0.001,
                    ),
                )
            ],
        ),
    ],
)
def test_parse_messages(bytes: bytes, expected: list[ParsedMessage]) -> None:
    # Arrange
    parser = MessageParser()

    # Act
    parsed_messages: list[ParsedMessage] = list(parser.parse_messages(data=bytes))

    # Assert
    for i, message in enumerate(parsed_messages):
        assert message == expected[i]


class TestParserEdgeCases:
    """Test parser behavior with malformed or edge case inputs."""

    def test_parse_empty_bids_asks(self) -> None:
        # Arrange
        parser = MessageParser()
        data = b'{"event_type":"book","asset_id":"123","market":"0xabc","bids":[],"asks":[],"timestamp":"1000","hash":"0x0"}'

        # Act
        messages = list(parser.parse_messages(data))

        # Assert
        assert len(messages) == 1
        assert messages[0].book is not None
        assert len(messages[0].book.bids) == 0
        assert len(messages[0].book.asks) == 0

    def test_parse_multiple_price_changes_empty(self) -> None:
        # Arrange
        parser = MessageParser()
        data = b'{"event_type":"price_change","market":"0xabc","price_changes":[],"timestamp":"1000"}'

        # Act
        messages = list(parser.parse_messages(data))

        # Assert
        assert len(messages) == 0

    def test_parse_unknown_event_type(self) -> None:
        # Arrange
        parser = MessageParser()
        data = b'{"event_type":"unknown_type","market":"0xabc","timestamp":"1000"}'

        # Act & Assert
        from src.exceptions import UnknownMessageType

        with pytest.raises(UnknownMessageType):
            list(parser.parse_messages(data))

    def test_parse_missing_market(self) -> None:
        # Arrange
        parser = MessageParser()
        data = b'{"event_type":"book","asset_id":"123","timestamp":"1000"}'

        # Act & Assert
        from src.exceptions import UnknownMarket

        with pytest.raises(UnknownMarket):
            list(parser.parse_messages(data))

    def test_parse_malformed_json(self) -> None:
        # Arrange
        parser = MessageParser()
        data = b'{"event_type":"book","invalid json'

        # Act & Assert
        with pytest.raises(Exception):
            list(parser.parse_messages(data))
