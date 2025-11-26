# Phase 2: AsyncIO Message Pipeline - "Non-Blocking I/O"

## Learning Objectives

Understand:
- Event loop mechanics and cooperative multitasking
- The difference between concurrency (asyncio) and parallelism (multiprocessing)
- When async helps and when it doesn't
- Queue-based backpressure patterns
- Why batching improves throughput

## System Specification

### What You're Adding

Transform your Phase 1 synchronous system to use asyncio:

```
Async Message Source → Async Queue → Async Message Processor → State Machine (still sync!)
                                            ↓
                                      Async Query Interface
```

**Key change:** Message I/O is now async, but state processing remains synchronous (for now).

### Functional Requirements

**R1: Async Message Ingestion**
- Convert message source to async generator using `async for`
- Messages should arrive continuously without blocking
- Support configurable artificial "network latency" (e.g., 10-50ms between messages)

**R2: Bounded Queue with Backpressure**
- Implement `asyncio.Queue` with configurable max size (e.g., 1000 messages)
- When queue is full, measure what happens
- Implement at least two backpressure strategies: drop messages OR slow down source

**R3: Batch Processing**
- Collect messages into batches before processing
- Support two batching modes:
  - **Size-based:** Process when batch reaches N messages
  - **Time-based:** Process every T milliseconds, regardless of batch size
- Measure: What batch size/timeout gives best throughput?

**R4: Concurrent Queries**
- Queries should not block message processing
- Use `asyncio.create_task()` to handle queries concurrently
- Measure: Can you handle 100 queries/second while processing messages?

### Design Constraints

1. **Use asyncio, but NOT threads or multiprocessing yet**
2. **State machine processing remains synchronous** (this is intentional!)
3. **Install and use uvloop**: `asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())`
4. **No database I/O yet** - keep everything in memory

## Architecture Guidance

### The Async Message Source

**Question:** How do you simulate network I/O with realistic characteristics?

Think about:
- Real networks have variable latency (use `random.uniform()`)
- Real networks can deliver bursts of messages
- How do you make this testable? (Hint: Inject timing behavior)

```python
async def message_stream(rate: int, jitter_ms: float = 10):
    """
    Your implementation here.
    
    Key decisions:
    - How do you control message arrival rate?
    - Should you use asyncio.sleep()? Why?
    - What happens if processing is slower than arrival rate?
    """
    pass
```

### The Queue Layer

The queue is your **buffer** between I/O and processing:

```python
message_queue = asyncio.Queue(maxsize=1000)
```

**Critical questions:**
1. What happens when the queue fills up?
2. Should you use `put_nowait()` or `await put()`? What's the difference?
3. How do you detect that backpressure is occurring?

**Design challenge:** Implement a custom queue that tracks:
- Current depth
- High water mark (peak depth seen)
- Number of items dropped (if using drop strategy)
- Average wait time for items in queue

### The Batch Collector

Batching is crucial for throughput. Here's the core challenge:

**Problem:** You want to collect N messages OR wait T seconds, whichever comes first.

```python
async def collect_batch(queue: asyncio.Queue, 
                       max_size: int = 100, 
                       timeout: float = 0.01) -> list[Message]:
    """
    Your implementation here.
    
    Hints:
    - asyncio.wait_for() is your friend
    - What happens at T seconds if you only have 5 messages?
    - Should you return an empty batch?
    """
    pass
```

**Question for you:** If processing a batch takes 50ms, what timeout value should you use? Why?

### The Processing Loop

Here's where it gets interesting. You now have:
- Async ingestion
- Async batching
- **Sync** state processing

How do you bridge these worlds?

**Option 1: `asyncio.to_thread()`**
```python
async def process_batch(batch: list[Message]):
    for msg in batch:
        await asyncio.to_thread(state_machine.process_message, msg)
```

**Option 2: `loop.run_in_executor()`**
```python
async def process_batch(batch: list[Message]):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, state_machine.process_message, msg)
```

**Question:** What's the difference? Which should you use and why?

**Deeper question:** If you have 10 messages in a batch and each takes 5ms to process, does async help here? Why or why not?

### The Query Handler

Queries can now run concurrently with message processing:

```python
async def handle_query(worker_id: str) -> dict:
    """
    What happens if you query while a message is being processed?
    
    Consider:
    - Is the state machine thread-safe?
    - Do you need locks?
    - What if processing is in the middle of updating multiple keys?
    """
    pass
```

**Design decision:** Should you add a lock around state access? What are the tradeoffs?

## Experiments to Run

### Experiment 1: Async vs Sync Ingestion

**Hypothesis:** Async message source allows higher throughput than sync.

**Test:**
1. Run Phase 1 sync system at various message rates
2. Run Phase 2 async system at same rates
3. Measure end-to-end latency at each rate

**Questions:**
- At what message rate do you see improvement?
- Why does async help (or not help)?
- Graph: Throughput vs Message Rate for both systems

### Experiment 2: Queue Sizing

**Hypothesis:** Larger queues smooth out bursts but increase latency.

**Test:**
1. Vary queue size: 10, 100, 1000, 10000
2. Send burst of 5000 messages, then pause, repeat
3. Measure: average latency, p99 latency, drops

**Questions:**
- What's the optimal queue size?
- How does queue size relate to latency?
- Can you derive a formula?

### Experiment 3: Batch Size Optimization

**Hypothesis:** Larger batches = higher throughput but higher latency.

**Test:**
1. Vary batch size: 1, 10, 50, 100, 500
2. Vary batch timeout: 1ms, 10ms, 100ms
3. Measure throughput and latency for all combinations

**Questions:**
- What's the sweet spot?
- Does the optimal batch size depend on processing time?
- Create a heatmap: Batch Size × Timeout → Throughput

### Experiment 4: The GIL Limitation

**Critical experiment:** Prove that CPU-bound work doesn't benefit from asyncio.

**Test:**
1. Replace `time.sleep()` with actual CPU work: `sum(range(1000000))`
2. Run with 1, 10, 100 concurrent message processors
3. Use `htop` or `psutil` to watch CPU usage

**Expected observation:** Single core at 100%, others idle.

**Question:** Why? Draw a diagram of how the event loop schedules tasks.

### Experiment 5: Concurrent Queries Under Load

**Test:**
1. Send messages at 80% of max throughput
2. Simultaneously send queries at 100/second
3. Measure impact on message processing latency

**Questions:**
- Do queries slow down message processing?
- Is this because of GIL contention?
- What if queries are more frequent than messages?

## Success Criteria

✅ **Async transformation complete:**
- Message source is async generator
- Queue-based buffering with configurable size
- Batch collection with timeout and size limits
- Queries don't block message ingestion

✅ **Performance improvements measured:**
- Can demonstrate higher message acceptance rate
- Can show reduced I/O blocking
- Can handle concurrent queries

✅ **GIL limitation understood:**
- Can explain why CPU-bound work doesn't speed up
- Can measure that only one core is used
- Can articulate what's needed next (spoiler: multiprocessing)

✅ **Design patterns internalized:**
- Understand when to use `await` vs `create_task()`
- Know how to handle timeouts with `wait_for()`
- Can implement proper cleanup with `TaskGroup` (Python 3.11+)

## Key Insights to Discover

By the end of Phase 2, you should viscerally understand:

1. **Asyncio gives you concurrency, not parallelism**
   - Great for I/O-bound workloads
   - Useless for CPU-bound workloads (because of GIL)
   
2. **Queues are your buffer against mismatch**
   - Producer/consumer rate differences
   - But unbounded queues lead to unbounded memory
   
3. **Batching is a throughput/latency tradeoff**
   - Larger batches = better throughput
   - But each message waits longer
   
4. **Backpressure is essential**
   - Without it, the system crashes under load
   - Must be designed in from the start

## Questions to Guide Implementation

**Before coding:**
1. Draw the dataflow: Where are the async boundaries?
2. Where will you use `await` vs `create_task()`?
3. What's your shutdown strategy? How do you gracefully stop?

**While implementing:**
1. When you call `await asyncio.sleep(0)`, what happens?
2. Why does the event loop need explicit yield points?
3. What happens if you call a blocking function without `to_thread()`?

**After implementation:**
1. Can you predict CPU usage from your code?
2. Why is throughput still limited?
3. What measurement proves asyncio isn't enough?

## Code Quality Requirements

- Use Python 3.11+ `TaskGroup` for structured concurrency
- Type hints on all functions
- Proper exception handling (catch in the right places)
- Graceful shutdown (cleanup resources)
- Use `asyncio.run()` as entry point

## Hints & Tips

**Hint 1: Event Loop Debugging**
Set `PYTHONASYNCIODEBUG=1` to catch common mistakes:
- Forgetting `await`
- Blocking the event loop
- Never-awaited coroutines

**Hint 2: Measuring Async Performance**
```python
# Don't do this:
start = time.time()
await some_operation()
elapsed = time.time() - start

# Do this:
start = asyncio.get_event_loop().time()
await some_operation()
elapsed = asyncio.get_event_loop().time() - start
```

**Hint 3: Batch Collection Pattern**
```python
async def collect_with_timeout(queue, max_size, timeout):
    batch = []
    deadline = loop.time() + timeout
    
    while len(batch) < max_size:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
            batch.append(item)
        except asyncio.TimeoutError:
            break
    
    return batch
```

## What's Next?

Phase 2 will prove that asyncio solves I/O blocking but doesn't help with CPU-bound work. Your measurements will show:
- Single core maxed out
- Other cores idle
- Throughput still limited by processing time

This **motivates Phase 3**: Breaking the GIL with multiprocessing.
