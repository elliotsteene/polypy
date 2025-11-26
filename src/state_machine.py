import asyncio
from dataclasses import dataclass
from datetime import datetime

from message_source import (
    BookMessage,
    Message,
    MessageEvent,
    OrderSide,
    OrderSummary,
    PriceChangeMessage,
)


@dataclass
class PriceLevel:
    price: float
    size: int


class StateMachine:
    def __init__(self, state_machine_id: int) -> None:
        self._id = state_machine_id
        # Bids (buy orders) - sorted descending (highest price first)
        self._bid_map: dict[float, int] = {}
        self._bids: list[PriceLevel] = []
        # Asks (sell orders) - sorted ascending (lowest price first)
        self._ask_map: dict[float, int] = {}
        self._asks: list[PriceLevel] = []

        self._queue: asyncio.Queue[Message | None] = asyncio.Queue(10_000)
        self._shutdown_event = asyncio.Event()

        self.message_latencies: list[float] = []

    async def run(self):
        while not self._shutdown_event.is_set():
            # message = await self._queue.get()
            messages = await self.collect_with_timeout(
                max_size=100,
                timeout=0.1,
            )
            # await asyncio.to_thread(self.receive, messages)
            self.receive(messages)

    def shutdown(self):
        self._shutdown_event.set()

    async def collect_with_timeout(
        self, max_size: int, timeout: float
    ) -> list[Message | None]:
        batch = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        while len(batch) < max_size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break

        return batch

    def put_nowwait_message(self, message: Message | None) -> None:
        self._queue.put_nowait(message)

    async def put_message(self, message: Message | None) -> None:
        await self._queue.put(message)

    def receive(self, messages: list[Message | None]) -> None:
        for message in messages:
            if not message:
                print(f"StateMachine {self._id}: shutdown received")
                self._queue.task_done()
                self.shutdown()
                continue

            match message.event_type:
                case MessageEvent.BOOK:
                    self._process_book_message(message=message)
                case MessageEvent.PRICE_CHANGE:
                    self._process_price_change(message=message)

            # time.sleep(0.005)
            sum(range(1_000_000))
            # time.sleep(0.025)
            self._queue.task_done()

            message_latency: int = (datetime.now() - message.timestamp).total_seconds()

            self.message_latencies.append(message_latency)

    def _process_book_message(self, message: Message) -> None:
        assert isinstance(message, BookMessage)
        # print(f"Worker {self._id}: Processing book message")

        for buy in message.buys:
            self._insert_price_level(
                price_levels=self._bids,
                price_map=self._bid_map,
                order=buy,
                descending=True,
            )

        for sell in message.sells:
            self._insert_price_level(
                price_levels=self._asks,
                price_map=self._ask_map,
                order=sell,
                descending=False,
            )

    def _process_price_change(self, message: Message) -> None:
        assert isinstance(message, PriceChangeMessage)
        # print(f"Worker {self._id}: Processing price change message")

        match message.side:
            case OrderSide.BUY:
                self._update_orderbook(
                    price_levels=self._bids,
                    price_map=self._bid_map,
                    price_change=message,
                    descending=True,
                )

            case OrderSide.SELL:
                self._update_orderbook(
                    price_levels=self._asks,
                    price_map=self._ask_map,
                    price_change=message,
                    descending=False,
                )

    def _update_orderbook(
        self,
        price_levels: list[PriceLevel],
        price_map: dict[float, int],
        price_change: PriceChangeMessage,
        descending: bool = False,
    ) -> None:
        # Validate size is non-negative
        if price_change.size < 0:
            print(
                f"Warning: Received negative size {price_change.size} for price {price_change.price}"
            )
            return

        # If message.size == 0, remove from array AND map
        # If message.price in map, update array size value
        # If message.price not in map, update map and add to array
        if price_change.size == 0:
            if price_change.price not in price_map:
                # Price doesn't exist - might indicate missed message or out-of-order processing
                print(
                    f"Warning: Attempted to remove non-existent price {price_change.price}"
                )
                return

            existing_idx = price_map[price_change.price]

            # Remove from map first
            del price_map[price_change.price]

            # Remove from array
            price_levels.pop(existing_idx)

            # Update indices for all prices after the removed index
            # Note: We iterate over items() which is safe since we're only modifying values
            for key in price_map:
                if price_map[key] > existing_idx:
                    price_map[key] -= 1
        else:
            # Size > 0: Update existing or insert new
            if price_change.price in price_map:
                # Price exists - replace the total size (not increment)
                # This represents the new total liquidity at this price level
                existing_idx = price_map[price_change.price]
                price_levels[existing_idx].size = price_change.size

            else:
                # New price level - insert into sorted order
                self._insert_price_level(
                    price_levels=price_levels,
                    price_map=price_map,
                    order=OrderSummary(
                        price=price_change.price, size=price_change.size
                    ),
                    descending=descending,
                )

    def _insert_price_level(
        self,
        price_levels: list[PriceLevel],
        price_map: dict[float, int],
        order: OrderSummary,
        descending: bool = False,
    ) -> None:
        """Generic method to insert a price level."""
        if order.price in price_map:
            # Price already exists - aggregate the size (for book messages)
            # This accumulates multiple orders at the same price level
            existing_idx = price_map[order.price]
            price_levels[existing_idx].size += order.size
            return

        price_level = PriceLevel(price=order.price, size=order.size)

        # Handle empty order book
        if len(price_levels) == 0:
            price_levels.append(price_level)
            price_map[order.price] = 0
            return

        # Find insertion position using binary search
        insert_idx = self._binary_search_insert_position(
            price_levels, order.price, descending=descending
        )

        # Insert the new price level
        price_levels.insert(insert_idx, price_level)

        # Update all indices at or after the insertion point
        # This is O(n) but necessary to keep the map synchronized
        for key, idx in price_map.items():
            if idx >= insert_idx:
                price_map[key] = idx + 1

        # Add the new price to the map
        price_map[order.price] = insert_idx

    def _binary_search_insert_position(
        self, arr: list[PriceLevel], value: float, descending: bool = False
    ) -> int:
        left, right = 0, len(arr)

        while left < right:
            mid = (left + right) // 2
            # Fixed: compare with .price attribute
            if descending:
                # For descending order (bids)
                if arr[mid].price > value:
                    left = mid + 1
                else:
                    right = mid
            else:
                # For ascending order (asks)
                if arr[mid].price < value:
                    left = mid + 1
                else:
                    right = mid

        return left
