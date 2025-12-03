from typing import Any, Generator, assert_never

import msgspec

from src.exceptions import UnknownMarket, UnknownMessageType
from src.messages.protocol import (
    EVENT_TYPE_MAP,
    BookSnapshot,
    EventType,
    LastTradePrice,
    ParsedMessage,
    PriceChange,
    PriceLevel,
    RawMessage,
)

# Pre-comiled decoder for raw json
_raw_decoder = msgspec.json.Decoder()


class MessageParser:
    def parse_messages(self, data: bytes) -> Generator[ParsedMessage]:
        """
        Parse raw Websocket message bytes into typed structure.
        """
        try:
            raw_messages = self._process_bytes(data)

            for raw_message in raw_messages:
                match raw_message.event_type:
                    case EventType.BOOK:
                        parsed_messages = self._parse_book_snapshot(raw_message)
                    case EventType.PRICE_CHANGE:
                        parsed_messages = self._parse_price_change(raw_message)
                    case EventType.LAST_TRADE_PRICE:
                        parsed_messages = self._parse_last_trade_price(raw_message)
                    case EventType.TICK_SIZE_CHANGE:
                        parsed_messages = self._parse_tick_size_change(raw_message)

                    # It should never reach these options
                    case EventType.UNKNOWN:
                        pass
                    case _:
                        assert_never(raw_message.event_type)

        except AssertionError as e:
            print(f"Unexpected event type received, unreachable code reached : {e}")
            raise

        except NotImplementedError:
            pass

        except Exception as e:
            print(e)
            raise

        return parsed_messages

    @staticmethod
    def _process_bytes(data: bytes) -> Generator[RawMessage]:
        try:
            raw: Any = _raw_decoder.decode(data)

            raw_messages = raw if isinstance(raw, list) else [raw]

            for message in raw_messages:
                event_type_str: str = message.get("event_type", "")
                event_type = EVENT_TYPE_MAP.get(event_type_str, EventType.UNKNOWN)

                if event_type == EventType.UNKNOWN:
                    raise UnknownMessageType(
                        f"Unknown message type received: {event_type_str}"
                    )

                market: str = message.get("market", "")
                if market == "":
                    raise UnknownMarket("Unable to parse market")

                timestamp = int(message.get("timestamp", 0))

                yield RawMessage(
                    event_type=event_type,
                    market=market,
                    timestamp=timestamp,
                    raw_data=message,
                )

        except Exception as e:
            print(e)
            raise

    @staticmethod
    def _parse_book_snapshot(raw_message: RawMessage) -> Generator[ParsedMessage]:
        bids = tuple(
            PriceLevel.from_strings(level["price"], level["size"])
            for level in raw_message.raw_data.get("bids", [])
        )
        asks = tuple(
            PriceLevel.from_strings(level["price"], level["size"])
            for level in raw_message.raw_data.get("asks", [])
        )
        book = BookSnapshot(
            # asset_id=raw_message.raw_data.get("asset_id", ""),
            # market=raw_message.market,
            # timestamp=raw_message.timestamp,
            hash=raw_message.raw_data.get("hash", ""),
            bids=bids,
            asks=asks,
        )

        yield ParsedMessage(
            event_type=raw_message.event_type,
            market=raw_message.market,
            asset_id=raw_message.raw_data.get("asset_id", ""),
            book=book,
            raw_timestamp=raw_message.timestamp,
        )

    @staticmethod
    def _parse_price_change(raw_message: RawMessage) -> Generator[ParsedMessage]:
        raw_price_changes: list[dict[str, str]] = raw_message.raw_data.get(
            "price_changes", []
        )

        price_changes = tuple(
            PriceChange.from_strings(
                asset_id=change["asset_id"],
                price=change["price"],
                size=change["size"],
                side=change["side"],
                hash=change["hash"],
                best_bid=change["best_bid"],
                best_ask=change["best_ask"],
            )
            for change in raw_price_changes
        )

        for price_change in price_changes:
            yield ParsedMessage(
                event_type=raw_message.event_type,
                asset_id=price_change.asset_id,
                market=raw_message.market,
                price_change=price_change,
                raw_timestamp=raw_message.timestamp,
            )

    @staticmethod
    def _parse_last_trade_price(raw_message: RawMessage) -> Generator[ParsedMessage]:
        last_trade_price = LastTradePrice.from_strings(
            price=raw_message.raw_data["price"],
            size=raw_message.raw_data["size"],
            side=raw_message.raw_data["side"],
        )

        yield ParsedMessage(
            event_type=raw_message.event_type,
            market=raw_message.market,
            asset_id=raw_message.raw_data["asset_id"],
            last_trade=last_trade_price,
            raw_timestamp=raw_message.timestamp,
        )

    @staticmethod
    def _parse_tick_size_change(raw_message: RawMessage) -> Generator[ParsedMessage]:
        raise NotImplementedError
