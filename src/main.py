import asyncio
import multiprocessing as mp

import structlog

from src.app import PolyPy
from src.core.logging import Logger
from src.core.logging import configure as configure_logging

configure_logging()

logger: Logger = structlog.get_logger()


# class App:
#     def __init__(self, asset_ids: list[str]) -> None:
#         self.store: OrderbookStore = OrderbookStore()

#         for id in asset_ids:
#             asset = Asset(id, "16r9tvhjks98r")
#             self.store.register_asset(asset)

#     async def message_callback(
#         self, connection_id: str, message: ParsedMessage
#     ) -> None:
#         # await logger.ainfo(
#         #     f"Message received {message.asset_id} - type: {message.event_type}"
#         # )
#         state = self.store.get_state(message.asset_id)
#         if not state:
#             return

#         received = datetime.now(timezone.utc)

#         if message.book:
#             state.apply_snapshot(message.book, message.raw_timestamp)
#         elif message.price_change:
#             state.apply_price_change(message.price_change, message.raw_timestamp)
#         else:
#             logger.info(f"No state needed to apply: {message.asset_id}")

#         process_latency = (datetime.now(timezone.utc) - received) / timedelta(
#             milliseconds=1
#         )

#         network_latency = (
#             received
#             - datetime.fromtimestamp(message.raw_timestamp // 1000, timezone.utc)
#         ) / timedelta(milliseconds=1)

#         logger.info(f"Network latency: {network_latency:.2f} ms")
#         logger.info(f"Process latency: {process_latency:.2f} ms")


async def main():
    logger.info("Starting PolyPy application...")
    max_workers = mp.cpu_count() - 1
    polypy = PolyPy(
        num_workers=max_workers,
        enable_http=True,
    )

    await polypy.run()
    # parser = MessageParser()
    # connection_id = f"conn-{uuid.uuid4().hex[:8]}"
    # asset_ids = [
    #     "74018646712472971445258547247048869505144598783748525202442089895996249694683",
    #     "21489772516410038586556744342392982044189999368638682594741395650226594484811",
    # ]

    # app = App(asset_ids)

    # connection = WebsocketConnection(
    #     connection_id=connection_id,
    #     asset_ids=asset_ids,
    #     message_parser=parser,
    #     on_message=app.message_callback,
    # )

    # await logger.ainfo(f"Starting websocket connection: {connection_id}")

    # loop = asyncio.get_event_loop()
    # shutdown_event = asyncio.Event()

    # def signal_handler(*args, **kwargs) -> None:
    #     logger.info("Shutdown signal received")
    #     shutdown_event.set()

    # for sig in (signal.SIGINT, signal.SIGTERM):
    #     loop.add_signal_handler(sig, signal_handler)

    # try:
    #     await connection.start()
    #     logger.info("Connection started")

    #     await shutdown_event.wait()

    # finally:
    #     await connection.stop()
    #     stats = connection.stats
    #     logger.info(f"Message rate: {stats.message_rate}")
    #     logger.info(f"Messages received: {stats.messages_received}")
    #     logger.info(f"Bytes received: {stats.bytes_received}")
    #     logger.info(f"Parse errors: {stats.parse_errors}")

    #     logger.info(f"Memory usage: {app.store.memory_usage()}")

    #     for sig in (signal.SIGINT, signal.SIGTERM):
    #         loop.remove_signal_handler(sig)

    await logger.ainfo("PolyPy finished")


if __name__ == "__main__":
    asyncio.run(main())
