# Phase 3: Process-Based Workers - "True Parallelism"

## Learning Objectives

Understand:
- The GIL and why it matters
- Process vs thread tradeoffs (memory, startup, IPC)
- Inter-process communication costs
- How to architect for process-based parallelism
- The serialization bottleneck

## System Specification

### What You're Adding

Each state machine now runs in its own process:

```
Main Process:
  Async Message Source → Queue → Router
                                    ↓
  Worker Process 1: ← Message → State Machine
  Worker Process 2: ← Message → State Machine
  Worker Process N: ← Message → State Machine
                                    ↓
                              Query Interface
```

**Key change:** State machines run in separate processes, achieving true parallelism.

### Functional Requirements

**R1: Process Pool Management**
- Create N worker processes (start with 4-8)
- Each worker runs its own event loop
- Workers should survive the entire program lifetime (not spawned per-message)

**R2: Inter-Process Message Passing**
- Use `multiprocessing.Queue` to send messages from main → workers
- Each worker should have its own input queue
- Measure: What's the cost of sending a message through a queue?

**R3: Load Balancing**
- Messages for a given `worker_id` always go to the same process
- Implement consistent hashing or simple modulo for distribution
- Support dynamic worker creation (if worker_id is new)

**R4: Process-Safe Querying**
- Queries from main process need to get state from worker processes
- Implement request/response pattern over queues
- Measure: What's the query latency now?

### Design Constraints

1. **Each worker is a separate process** with its own Python interpreter
2. **No shared memory yet** - rely on queues for all IPC
3. **Messages must be picklable** - this will hurt performance (intentionally!)
4. **Workers should be long-lived** - don't spawn/kill on every message

## Architecture Guidance

### Process Creation Strategy

You have choices for how to spawn workers:

**Option 1: `multiprocessing.Process`**
```python
def worker_main(worker_id: str, input_queue: mp.Queue):
    # Worker runs in separate process
    # What happens to uvloop here?
    # Does each worker need its own event loop?
    pass

process = mp.Process(target=worker_main, args=(worker_id, queue))
process.start()
```

**Option 2: `multiprocessing.Pool`**
```python
pool = mp.Pool(processes=8)
# But how do you maintain state across invocations?
```

**Question:** Which should you use? What are the tradeoffs?

**Critical decision:** Should workers be generic (any worker can process any message) or specialized (each worker handles specific worker_ids)?

### Inter-Process Communication

The core challenge: Getting data between processes.

**For sending messages:**
```python
# Main process
worker_queue.put(message)

# Worker process  
message = worker_queue.get()  # Blocks until available
```

**Question:** What happens to the message object when you put it in the queue?

**Experiment:** Send a large message (1MB payload) vs small message (100 bytes). Measure the difference.

### The Routing Problem

Given a message, which worker should process it?

```python
class WorkerRouter:
    def __init__(self, num_workers: int):
        self.num_workers = num_workers
        self.worker_queues: list[mp.Queue] = []
        self.worker_processes: list[mp.Process] = []
        
    def route_message(self, message: Message) -> None:
        # Which queue should this go to?
        # How do you ensure the same worker_id always goes to same process?
        pass
```

**Design choices:**
1. **Round-robin:** Simple, but same worker_id might go to different processes
2. **Hash-based:** `hash(worker_id) % num_workers` - ensures consistency
3. **Explicit mapping:** Maintain a dictionary of worker_id → process

**Question:** Why does consistency matter? What breaks if the same worker_id goes to different processes?

### State Machine in Worker Process

Each worker process needs its own registry of state machines:

```python
def worker_process_main(worker_id: str, input_queue: mp.Queue, output_queue: mp.Queue):
    # Create registry of state machines for this process
    state_machines: dict[str, StateMachine] = {}
    
    # Install uvloop in worker process
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    
    # Start event loop
    asyncio.run(worker_loop(input_queue, output_queue, state_machines))

async def worker_loop(input_queue, output_queue, state_machines):
    loop = asyncio.get_event_loop()
    
    while True:
        # Get message from queue (blocking, so run in executor)
        message = await loop.run_in_executor(None, input_queue.get)
        
        # Get or create state machine
        if message.worker_id not in state_machines:
            state_machines[message.worker_id] = StateMachine(message.worker_id)
        
        # Process message
        sm = state_machines[message.worker_id]
        await loop.run_in_executor(None, sm.process_message, message)
```

**Questions:**
1. Why do we need `run_in_executor()` for `input_queue.get()`?
2. What happens if 100 different worker_ids hash to the same process?
3. Should you limit the number of state machines per process?

### Query Implementation

Querying is now harder because state lives in different processes:

**Pattern: Request/Response over queues**

```python
# Main process
def query_state(worker_id: str) -> dict:
    # 1. Determine which process has this worker
    process_idx = hash(worker_id) % num_workers
    
    # 2. Send query request
    query_id = generate_unique_id()
    request_queues[process_idx].put(QueryRequest(query_id, worker_id))
    
    # 3. Wait for response
    response = response_queue.get(timeout=1.0)  # Blocks!
    
    return response.state

# Worker process
async def handle_query(request: QueryRequest, state_machines, response_queue):
    sm = state_machines.get(request.worker_id)
    if sm:
        state = sm.get_state()
        response_queue.put(QueryResponse(request.query_id, state))
```

**Problem:** This blocks the main process. How can you make it async?

**Challenge:** Implement an async query mechanism that doesn't block the event loop.

### Process Lifecycle Management

Critical questions:
1. **Startup:** What order do you start things in?
2. **Shutdown:** How do you gracefully stop all workers?
3. **Failure:** What if a worker process crashes?

**Graceful shutdown pattern:**
```python
def shutdown():
    # 1. Stop sending new messages
    # 2. Wait for queues to drain
    # 3. Send shutdown signal to workers
    # 4. Join processes with timeout
    # 5. Kill if still alive
    pass
```

## Experiments to Run

### Experiment 1: Scaling with CPU Cores

**Hypothesis:** Throughput scales linearly with number of worker processes (up to CPU core count).

**Test:**
1. Run with 1, 2, 4, 8, 16 worker processes
2. Send messages at maximum rate
3. Measure throughput and CPU utilization

**Questions:**
- At what point does adding processes stop helping?
- Why? (Hint: Check CPU core count with `os.cpu_count()`)
- Graph: Throughput vs Number of Processes

### Experiment 2: IPC Overhead

**Hypothesis:** Queue-based IPC has significant overhead.

**Test:**
1. Measure time to put/get a message from a queue
2. Vary message size: 100 bytes, 1KB, 10KB, 100KB, 1MB
3. Compare to in-process function call

**Questions:**
- How much slower is IPC than a function call?
- How does message size affect IPC time?
- Where does the time go? (Hint: Serialization!)

### Experiment 3: Serialization Cost

**Critical experiment:** Prove that pickling is the bottleneck.

**Test:**
1. Create messages with different payload types:
   - Simple dict with primitives
   - Complex nested objects
   - Large numpy arrays
2. Measure time to `pickle.dumps()` and `pickle.loads()`

**Questions:**
- Which data types are expensive to serialize?
- Can you measure pickle vs unpickle time?
- What percentage of total latency is serialization?

### Experiment 4: Load Balancing Effectiveness

**Test:**
1. Send 10,000 messages with random worker_ids
2. Track which process handles each message
3. Measure per-process message counts and latency

**Questions:**
- Is load evenly distributed?
- What if worker_ids follow a Zipfian distribution?
- Does imbalance affect latency?

### Experiment 5: Query Latency

**Test:**
1. Query state while processing messages at various rates
2. Measure query latency at different system loads

**Questions:**
- How much slower are queries now vs Phase 2?
- Why the increase?
- Can you break down latency into components?

## Success Criteria

✅ **Multi-process architecture working:**
- Workers run in separate processes
- Messages routed to correct workers
- Each worker can process messages concurrently
- Proper startup and shutdown

✅ **True parallelism achieved:**
- Multiple CPU cores utilized
- Throughput scales with worker count (up to core limit)
- Can measure parallel execution

✅ **Serialization bottleneck identified:**
- Can measure pickle overhead
- Understand that this is the next bottleneck
- Have data proving it

✅ **Process management patterns learned:**
- Know how to spawn and manage processes
- Understand graceful shutdown
- Can handle process failures

## Key Insights to Discover

1. **The GIL is defeated** - True parallelism achieved
   - Multiple cores processing simultaneously
   - Throughput scales with cores
   
2. **But IPC is expensive** - New bottleneck emerges
   - Pickling/unpickling takes significant time
   - Message size matters a lot
   
3. **Process overhead is real** - Not free
   - Memory per process (40-50MB per Python interpreter)
   - Context switching costs
   
4. **Consistency is crucial** - Same worker_id → same process
   - Otherwise state gets split across processes
   - Leads to incorrect results

## Questions to Guide Implementation

**Architecture questions:**
1. Should each worker process handle multiple worker_ids or just one?
2. How do you prevent queue overflow in workers?
3. What happens if a worker dies mid-message?

**Implementation questions:**
1. How do you share the routing logic between main and workers?
2. Should query responses have their own queue or share one?
3. How do you correlate query requests with responses?

**Performance questions:**
1. What's the break-even point where multiprocessing wins over single-process?
2. How do you minimize time spent pickling?
3. Should you use pickle protocol 4 or 5? Does it matter?

## Common Pitfalls

**Pitfall 1: Forgetting to reinstall uvloop in worker**
```python
# Wrong: Only installed in main process
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
process.start()

# Right: Install in worker process
def worker_main():
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(worker_loop())
```

**Pitfall 2: Sharing queue references incorrectly**
- Queues must be created before processes are spawned
- Can't create queue in main and pass to worker after spawn

**Pitfall 3: Not handling poison pills for shutdown**
```python
# Send shutdown signal to all workers
for queue in worker_queues:
    queue.put(SHUTDOWN_SIGNAL)
```

## Hints & Tips

**Hint 1: Use `spawn` start method**
```python
if __name__ == "__main__":
    mp.set_start_method('spawn')  # More reliable than fork
```

**Hint 2: Debug IPC issues**
```python
# Add logging on both sides of queue
logger.info("Putting message", message_id=msg.id)
message_queue.put(msg)

# In worker
msg = queue.get()
logger.info("Received message", message_id=msg.id)
```

**Hint 3: Measure pickle time**
```python
import pickle
import time

data = create_large_message()

start = time.perf_counter()
serialized = pickle.dumps(data)
pickle_time = time.perf_counter() - start

start = time.perf_counter()
deserialized = pickle.loads(serialized)
unpickle_time = time.perf_counter() - start
```

## What's Next?

Phase 3 achieves true parallelism but reveals serialization as the bottleneck. Your measurements will show:
- Pickle overhead dominates for large messages
- Query latency increased significantly
- Memory usage multiplied by process count

This **motivates Phase 4**: Zero-copy state access via shared memory.
