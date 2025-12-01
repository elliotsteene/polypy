import asyncio
import multiprocessing as mp
import queue
from multiprocessing.connection import Connection

from logging_config import get_logger
from message_source import Message
from state_machine import StateMachine

logger = get_logger(__name__)

type WorkerMessage = Message | None
type WorkerMPQueue = mp.Queue[WorkerMessage]


class Worker:
    def __init__(
        self,
        queue: WorkerMPQueue,
        state_machine: StateMachine,
    ):
        self._queue = queue
        self._shutdown_event = asyncio.Event()

        self._state_machine: StateMachine = state_machine
        self._state_machine_tasks: set[asyncio.Task] = set()

    def shutdown(self):
        self._shutdown_event.set()

    def run(self) -> None:
        try:
            while not self._shutdown_event.is_set():
                try:
                    message: WorkerMessage = self._queue.get(timeout=0.1)

                    if message is None:
                        break

                    self._state_machine.receive(message=message)

                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"An error occurred! {e}")
                    raise
        finally:
            logger.debug(
                f"Worker shutting down, has msgs remaining {not self._queue.empty()}"
            )


def start_worker(queue: WorkerMPQueue, pipe: Connection) -> None:
    logger.debug("starting worker!")

    state_machine = StateMachine()
    worker = Worker(queue=queue, state_machine=state_machine)
    worker.run()

    logger.debug("sending latencies for analysis")
    pipe.send(worker._state_machine.message_latencies)
