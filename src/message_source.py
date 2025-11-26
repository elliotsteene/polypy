import asyncio
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import AsyncGenerator

from logging_config import get_logger

logger = get_logger(__name__)


class MessageEvent(StrEnum):
    BOOK = "book"
    PRICE_CHANGE = "price_change"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderSummary:
    price: float
    size: int


@dataclass
class Message:
    worker_id: int
    timestamp: datetime
    event_type: MessageEvent


@dataclass
class BookMessage(Message):
    buys: list[OrderSummary] = field(default_factory=list)
    sells: list[OrderSummary] = field(default_factory=list)


@dataclass
class PriceChangeMessage(Message):
    price: float
    size: int
    side: OrderSide


class MessageSource:
    def __init__(self, max_workers: int = 10) -> None:
        self._max_workers = max_workers
        self._worker_message_counter = Counter[int]()

    async def message_stream(
        self,
        rate: int,
        duration: int,
    ) -> AsyncGenerator[Message, None]:
        start_time = datetime.now()
        wall_clock_start = time.time()  # Add this
        messages_generated = 0
        actual_elapsed = 0

        try:
            while (datetime.now() - start_time).total_seconds() < duration:
                worker_id = int(random.uniform(0, self._max_workers))
                message_type = self._get_message_type(worker_id)
                match message_type:
                    case MessageEvent.BOOK:
                        message = self._build_order_book(worker_id)
                    case MessageEvent.PRICE_CHANGE:
                        message = self._build_price_change(worker_id)

                self._worker_message_counter[worker_id] += 1
                loop_start = time.time()
                yield message
                loop_end = time.time()
                actual_elapsed += loop_end - loop_start
                messages_generated += 1
                await asyncio.sleep(1 / rate)
        finally:
            wall_clock_end = time.time()  # Add this
            wall_clock_elapsed = wall_clock_end - wall_clock_start  # Add this

            logger.info("Finished message stream")
            logger.info(f"Generated {messages_generated} messages")
            logger.info(f"Time spent in loop: {actual_elapsed:.2f}s")
            logger.info(f"Wall clock time: {wall_clock_elapsed:.2f}s")  # Add this
            logger.info(f"Expected messages at {rate} msg/s: {duration * rate}")
            logger.info(
                f"Actual rate: {messages_generated / wall_clock_elapsed:.1f} msg/s"
            )  # Add this

    def _get_message_type(self, worker_id: int) -> MessageEvent:
        curr_count = self._worker_message_counter[worker_id]
        return MessageEvent.BOOK if curr_count == 0 else MessageEvent.PRICE_CHANGE

    def _build_order_book(self, worker_id: int) -> BookMessage:
        buys = [self._create_order_summary(0.5, 1) for _ in range(10)]
        sells = [self._create_order_summary(0, 0.5) for _ in range(10)]
        return BookMessage(
            worker_id=worker_id,
            timestamp=datetime.now(),
            event_type=MessageEvent.BOOK,
            buys=buys,
            sells=sells,
        )

    def _create_order_summary(self, low: float, high: float) -> OrderSummary:
        return OrderSummary(
            price=round(random.uniform(low, high), 2),
            size=int(random.uniform(100, 500)),
        )

    def _build_price_change(self, worker_id: int) -> PriceChangeMessage:
        side = OrderSide(random.choice(list(OrderSide)).value)
        match side:
            case OrderSide.BUY:
                low, high = 0.5, 1
            case OrderSide.SELL:
                low, high = 0, 0.5

        return PriceChangeMessage(
            worker_id=worker_id,
            timestamp=datetime.now(),
            event_type=MessageEvent.PRICE_CHANGE,
            price=round(random.uniform(low, high), 2),
            size=int(random.uniform(100, 500)),
            side=side,
        )
