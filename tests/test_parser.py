import pytest

from src.parser import MessageParser
from src.protocol import (
    BookSnapshot,
    EventType,
    LastTradePrice,
    ParsedMessage,
    PriceChange,
    PriceLevel,
    Side,
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
                                size=30,
                            ),
                            PriceLevel(
                                price=490,
                                size=20,
                            ),
                        ),
                        asks=(
                            PriceLevel(
                                price=520,
                                size=25,
                            ),
                            PriceLevel(
                                price=530,
                                size=60,
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
                        size=200,
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
                        size=200,
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
                        size=219,
                        side=Side.BUY,
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
