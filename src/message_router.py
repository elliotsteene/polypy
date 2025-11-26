import asyncio
import multiprocessing as mp
from collections import Counter
from datetime import datetime
from multiprocessing.connection import Connection

from codetiming import Timer

from message_source import Message
from worker import WorkerMPQueue, start_worker


class MessageRouter:
    def __init__(self, max_queue_size=1000, max_sm_threshold: int = 5) -> None:
        self._queue: asyncio.Queue[Message | None] = asyncio.Queue(max_queue_size)
        self.queue_waittime: list[float] = []
        self._shutdown_event = asyncio.Event()

        # self._state_machine_mapping: dict[int, StateMachine] = {}
        # self._state_machine_tasks: set[asyncio.Task] = set()

        self._max_sm_threshold = max_sm_threshold
        self._process_queue_map: dict[str, WorkerMPQueue] = {}
        self._sm_process_map: dict[int, str] = {}
        self._process_sm_counter: Counter[str] = Counter()

        self._process_set: set[mp.Process] = set()
        self._worker_conn: list[Connection] = []

        self.message_latencies: list[float] = []

    # def deinit(self, _: asyncio.Task) -> None:
    #     for task in self._state_machine_tasks:
    #         task.cancel()

    def shutdown(self):
        self._shutdown_event.set()

    async def route_messages(self) -> None:
        print("Starting to pull messages...")
        try:
            while not self._shutdown_event.is_set():
                try:
                    message: Message | None = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=0.1,
                    )

                    if message is None:
                        # print("Message router: shutdown received")
                        # Signal all StateMachines to shutdown
                        for queue in self._process_queue_map.values():
                            queue.put(None)
                            queue.close()

                        self.aggregate_message_latency()

                        for process in self._process_set:
                            process.terminate()
                        # print("Process shutdown complete")
                        break

                    queue_time = datetime.now()
                    worker_queue = self._get_state_machine_process_queue(
                        message.worker_id
                    )

                    with Timer("processing", logger=None):
                        # await asyncio.to_thread(state_machine.receive, message)
                        # await asyncio.to_thread(state_machine.put_nowwait_message, message)
                        worker_queue.put(message)

                    self.queue_waittime.append(
                        (queue_time - message.timestamp).total_seconds()
                    )
                except asyncio.TimeoutError:
                    # No message available, loop back to check shutdown flag
                    continue

        except asyncio.CancelledError:
            print(
                f"Async Cancelled - Router cancelled with {self._queue.qsize()} messages remaining"
            )
            raise  # Re-raise to propagate cancellation

        finally:
            print(f"Router shutting down, {self._queue.qsize()} msgs remaining")

    def put_nowwait_message(self, message: Message | None) -> None:
        self._queue.put_nowait(message)

    async def put_message(self, message: Message | None) -> None:
        await self._queue.put(message)

    def _get_state_machine_process_queue(self, id: int) -> WorkerMPQueue:
        try:
            if id not in self._sm_process_map:
                self._distribute_id_to_process(id=id)

            process_name = self._sm_process_map[id]
            queue = self._process_queue_map[process_name]

        except KeyError:
            print("Worker does not exist in process map!")
            raise
        except Exception as e:
            print(f"An error occurred getting process queue: {e}")
            raise

        return queue

    def _distribute_id_to_process(self, id: int) -> None:
        if len(self._process_queue_map.keys()) == 0:
            process_name = None
        else:
            process_name = self._find_process_with_capacity()

        return self._register_worker_with_process(id=id, process_name=process_name)

    def _find_process_with_capacity(self) -> str | None:
        process_with_capacity: str | None = None

        for process_name in self._process_queue_map.keys():
            curr_sm_count = self._process_sm_counter[process_name]
            if curr_sm_count < self._max_sm_threshold:
                process_with_capacity = process_name
                break

        return process_with_capacity

    def _register_worker_with_process(self, id: int, process_name: str | None) -> None:
        if not process_name:
            process_name = self._create_worker_resources()

        # Tell worker to create state machine for worker ID
        queue = self._process_queue_map[process_name]
        queue.put(id)

        # Register resources and increment
        self._sm_process_map[id] = process_name
        self._process_sm_counter[process_name] += 1

    def _create_worker_resources(self) -> str:
        print("creating new process worker")
        # Create resources
        queue: WorkerMPQueue = mp.Queue()
        rec_conn, send_conn = mp.Pipe(duplex=False)
        self._worker_conn.append(rec_conn)
        process = mp.Process(
            target=start_worker,
            args=(
                queue,
                send_conn,
            ),
        )
        process.start()
        self._process_set.add(process)

        process_name = process.name
        self._process_queue_map[process_name] = queue

        return process_name

    def aggregate_message_latency(self) -> None:
        for conn in self._worker_conn:
            worker_latencies: list[float] = conn.recv()
            self.message_latencies.extend(worker_latencies)
