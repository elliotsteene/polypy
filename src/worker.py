import asyncio
import multiprocessing as mp
import queue
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from multiprocessing.connection import Connection

import uvloop

from logging_config import get_logger
from message_source import Message
from state_machine import StateMachine

logger = get_logger(__name__)

type WorkerMessage = Message | int | None
type WorkerMPQueue = mp.Queue[WorkerMessage]


class Worker:
    def __init__(
        self,
        queue: WorkerMPQueue,
    ):
        self._queue = queue
        self._shutdown_event = asyncio.Event()

        self._state_machine_mapping: dict[int, StateMachine] = {}
        self._state_machine_tasks: set[asyncio.Task] = set()

    def shutdown(self):
        self._shutdown_event.set()

    async def _run(self) -> None:
        loop = asyncio.get_event_loop()

        with ThreadPoolExecutor() as pool:
            try:
                while not self._shutdown_event.is_set():
                    try:
                        getP = partial(self._queue.get, timeout=0.1)
                        message: WorkerMessage = await loop.run_in_executor(pool, getP)

                        if message is None:
                            # Signal all StateMachines to shutdown
                            for sm in self._state_machine_mapping.values():
                                await sm.put_message(None)
                                await sm._queue.join()
                            # print("Worker shutdown complete")
                            break

                        if (
                            isinstance(message, int)
                            and message not in self._state_machine_mapping
                        ):
                            self._start_state_machine(id=message)
                            continue

                        assert isinstance(message, Message)

                        state_machine = self._state_machine_mapping[message.worker_id]
                        await state_machine.put_message(message=message)

                    except queue.Empty:
                        continue

                    except AssertionError as e:
                        logger.error(f"Unexpected message type in worker: {e}")
                        raise
                    except Exception as e:
                        logger.error(f"An error occurred! {e}")
                        raise
            except asyncio.CancelledError:
                logger.warning(
                    f"Async error - worker shutting down, has msgs remaining {not self._queue.empty()}"
                )
                raise  # Re-raise to propagate cancellation

            finally:
                logger.debug(
                    f"Worker shutting down, has msgs remaining {not self._queue.empty()}"
                )

    def _start_state_machine(self, id: int) -> None:
        # print(f"starting statemachine: {id}")
        sm = StateMachine(state_machine_id=id)
        task = asyncio.create_task(sm.run())

        self._state_machine_mapping[id] = sm
        self._state_machine_tasks.add(task)


def start_worker(queue: WorkerMPQueue, pipe: Connection) -> None:
    logger.debug("starting worker!")
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    worker = Worker(queue=queue)
    asyncio.run(worker._run())
    logger.debug("sending latencies for analysis")
    latencies: list[float] = []
    for sm in worker._state_machine_mapping.values():
        latencies.extend(sm.message_latencies)
    pipe.send(latencies)
