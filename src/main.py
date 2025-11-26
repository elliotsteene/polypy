import asyncio
import json
import multiprocessing as mp
from collections import defaultdict
from dataclasses import dataclass
from statistics import quantiles

import uvloop

from logging_config import get_logger, setup_logging
from message_router import MessageRouter
from message_source import MessageSource

# Set up logging before anything else
setup_logging()
logger = get_logger(__name__)

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


@dataclass
class Latency:
    rate: int
    messages_yielded: int
    p50: float
    p90: float
    p99: float


async def run(rate: int) -> Latency:
    logger.info(f"Starting run - rate: {rate}")
    num_workers = 50
    message_source = MessageSource(max_workers=num_workers)
    message_router = MessageRouter(num_workers=num_workers, max_queue_size=10_000)

    logger.info("Processing messages...")
    total_yielded_messages = 0
    async with asyncio.TaskGroup() as tg:
        tg.create_task(message_router.route_messages())

        async for message in message_source.message_stream(rate=rate, duration=5):
            # for message in messages:
            total_yielded_messages += 1
            try:
                # await asyncio.to_thread(message_router.put_nowwait_message, message)
                await message_router.put_message(message)
            except asyncio.QueueFull:
                logger.warning("Dropping message")

        # Signal shutdown
        await message_router.put_message(None)

    # latencies = message_router.aggregate_message_latency()
    latencies = message_router.message_latencies
    logger.info(f"{len(latencies)} message with latency")
    # latencies = [0, 0]
    # Calculate percentiles
    percentiles = quantiles(latencies, n=100)
    p50 = percentiles[49]  # 50th percentile (same as median)
    p90 = percentiles[89]  # 90th percentile
    p99 = percentiles[98]  # 99th percentile

    return Latency(
        rate=rate,
        messages_yielded=total_yielded_messages,
        p50=p50,
        p90=p90,
        p99=p99,
    )


async def main():
    logger.info("Starting message stream")
    rate = 1000
    latencies: dict[int, Latency] = {}
    # for _ in range(1, 6):
    for _ in range(1, 4):
        latency = await run(rate)

        latencies[rate] = latency

        rate = rate * 10

    results = defaultdict(lambda: defaultdict(int))
    for rate, latency in latencies.items():
        results["p50"][rate] = latency.p50
        results["p90"][rate] = latency.p90
        results["p99"][rate] = latency.p99

    # Convert defaultdict to dict for cleaner JSON output
    results_dict = {k: dict(v) for k, v in results.items()}
    logger.info(f"Latency results:\n{json.dumps(results_dict, indent=2)}")

    # Now process them and measure how long it actually takes
    # print("Processing messages...")
    # router_task = asyncio.create_task(message_router.route_messages())
    # router_task.add_done_callback(message_router.deinit)

    # print(f"Yieled messages: {total_yielded_messages}")

    # print(f"Mean processing: {Timer.timers.mean('processing')}")
    # print(f"Median processing: {Timer.timers.median('processing')}")
    # print(f"Stdev: {Timer.timers.stdev('processing')}")
    #
    # print(f"Min: {Timer.timers.min('processing')}")
    # print(f"Max: {Timer.timers.max('processing')}")

    # latencies = message_router.aggregate_message_latency()
    # queue_waittime = message_router.queue_waittime

    # Calculate percentiles
    # percentiles = quantiles(latencies, n=100)
    # p50 = percentiles[49]  # 50th percentile (same as median)
    # p90 = percentiles[89]  # 90th percentile
    # p99 = percentiles[98]  # 99th percentile

    # print(f"Mean latency: {mean(latencies)}")
    # print(f"Median latency: {median(latencies)}")
    # print(f"P50 latency: {p50}")
    # print(f"P90 latency: {p90}")
    # print(f"P99 latency: {p99}")
    # print(f"Stdev latency: {stdev(latencies)}")
    # print(f"Min latency: {min(latencies)}")
    # print(f"Max latency: {max(latencies)}")

    # print(f"Mean waittime: {mean(queue_waittime)}")
    # print(f"Median waittime: {median(queue_waittime)}")
    # print(f"Stdev waittime: {stdev(queue_waittime)}")
    # print(f"Min waittime: {min(queue_waittime)}")
    # print(f"Max waittime: {max(queue_waittime)}")


if __name__ == "__main__":
    mp.set_start_method("spawn")
    uvloop.run(main())
