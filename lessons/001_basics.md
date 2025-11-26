# Phase 1: Single-Threaded Foundation - "Feel The Pain"

## Learning Objectives

By the end of this phase, you'll viscerally understand:

- Where synchronous I/O blocks your entire program
- How the GIL prevents CPU parallelism (even with threads)
- Why naive state management creates bottlenecks
- The performance baseline that motivates everything in later phases

---

## System Specification

### What You're Building

A minimal message processing system with three components:

```
Message Source → Message Processor → State Machine → Query Interface
```

#### Flow

1. Messages arrive from a network source (simulate with a generator for now)
2. Each message contains: `worker_id`, `payload`, `timestamp`
3. Route message to the appropriate state machine based on `worker_id`
4. State machine processes the message and updates its internal state
5. Users can query the current state of any worker at any time

---

## Functional Requirements

### R1: Message Ingestion

- Accept messages at a configurable rate (start with 100 msg/s)
- Parse message into structured format
- Must not drop messages

### R2: State Management

- Support multiple independent state machines (start with 10)
- Each state machine maintains a dictionary of key-value pairs
- Processing a message updates 1-5 keys in the state
- State updates should simulate ~5ms of CPU work (use `time.sleep` or actual computation)

### R3: Query Interface

- Any state machine's current state can be queried at any time
- Queries should return the full state dictionary
- Must work via a simple HTTP endpoint or function call

---

## Design Constraints (These Are Critical!)

- **Single-threaded execution only** - No threading, no asyncio, no multiprocessing yet
- **Blocking I/O** - Use regular `time.sleep()`, synchronous sockets
- **In-memory only** - No databases, no external state stores
- **Measure everything** - Track latency, throughput, and blocking behavior

---

## Architecture Guidance

### Component 1: Message Source

**Think about:**

- How will you simulate a continuous stream of messages?
- What happens when message generation is faster than processing?
- How do you make the rate configurable for experimentation?

**Key design principle:** The source should be independent of processing speed. In the real world, messages arrive whether you're ready or not.

---

### Component 2: Message Router

This is simpler than it sounds:

- Given a message, determine which state machine should handle it
- How will you map `worker_id` to state machine instances?

**Question for you:** Should you create state machines lazily (on first message) or pre-allocate them? What are the tradeoffs?

---

### Component 3: State Machine

This is where the "work" happens:

```python
class StateMachine:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.state: dict = {}
    
    def process_message(self, message: Message) -> None:
        # Your implementation here
        pass
    
    def get_state(self) -> dict:
        # Your implementation here
        pass
```

**Design decisions you need to make:**

- What should the state dictionary contain? (Keep it simple - maybe counters, timestamps, computed values)
- How will you simulate realistic processing time? (Hint: Mix CPU work and I/O simulation)
- Should state updates be atomic? (For now, don't worry about concurrency)

**Implementation idea:** Have the message payload contain operations like:

```python
{"op": "increment", "key": "counter"}
{"op": "set", "key": "status", "value": "processing"}
{"op": "compute", "key": "result", "value": <some CPU-bound calculation>}
```

---

### Component 4: Query Interface

Keep this dead simple for Phase 1:

```python
def query_state(worker_id: str) -> dict:
    # Return current state for worker
    pass
```

**Critical question:** When a query happens while a message is being processed, what should be returned?

---

## Measurement Requirements

You **MUST** instrument your code to answer these questions:

1. **End-to-end latency:** Time from message generation → state updated
2. **Processing latency:** Time spent in `process_message()`
3. **Blocking time:** When does the message source have to wait?
4. **Throughput:** Messages processed per second
5. **Query latency:** How long does `query_state()` take?

**Suggested approach:** Use a simple `@timed` decorator and collect metrics in a list:

```python
metrics = {
    "message_latencies": [],
    "processing_times": [],
    "query_times": [],
}
```

---

## Experiments to Run

Once you have a working implementation, systematically explore the breaking points:

### Experiment 1: Throughput Limits

- Start with 10 msg/s, gradually increase to 1000 msg/s
- What happens to latency as you approach the system's limit?
- At what message rate does the queue start growing unbounded?

### Experiment 2: Processing Time Impact

- Vary the simulated processing time (1ms, 5ms, 10ms, 50ms)
- How does this affect maximum throughput?
- Calculate: What's the theoretical maximum throughput based on processing time?

### Experiment 3: Query Interference

- Run queries while messages are being processed
- Do queries block message processing? Or does message processing block queries?
- Measure: What's the p99 query latency under load?

### Experiment 4: Multiple Workers

- Increase the number of state machines from 10 → 100 → 1000
- Does this affect per-worker performance?
- Why or why not?

---

## Success Criteria

You've completed Phase 1 when:

### ✅ Functionally complete

- Messages are processed in order
- State is correctly updated
- Queries return current state
- No crashes under normal load

### ✅ Fully instrumented

- Can produce latency percentiles (p50, p95, p99)
- Can graph throughput over time
- Can measure blocking behavior

### ✅ Understanding achieved

- You can explain exactly where the bottleneck is
- You can predict maximum throughput from processing time
- You can articulate why queries interfere with message processing

---

## Questions to Guide Your Implementation

### Before you start coding

1. What data structure will you use to store the state machines? (dictionary, list, something else?)
2. How will you handle the "main loop" that keeps pulling messages?
3. Should you process all messages for one worker before moving to the next, or round-robin?

### While implementing

1. When you call `time.sleep()` to simulate work, what does the rest of your program do?
2. If you have 10 workers and messages arrive for worker #7, do the other 9 workers sit idle?
3. Where exactly is the blocking happening? (Put print statements to find out!)

### After initial implementation

1. What's the theoretical maximum throughput if processing takes 5ms per message?
2. Why is your actual throughput lower than the theoretical maximum?
3. If you wanted 10x higher throughput with this architecture, what would you need?

---

## Hints & Tips

### Hint 1: Message Generation

You don't need real networking yet. A generator function works great:

```python
def message_stream(rate: int, duration: int):
    # Yield messages at specified rate
    # Think: How do you control timing?
```

### Hint 2: Timing Measurements

Context managers are your friend:

```python
with Timer("processing"):
    state_machine.process_message(msg)
```

### Hint 3: State Machine Logic

Start with something trivial (increment counters), then add complexity. The actual logic doesn't matter - the timing does.

---

## What's Next?

After Phase 1, you'll have hard data showing:

- "Processing is CPU-bound, not I/O-bound"
- "Queries and updates can't happen simultaneously"
- "Throughput is limited by single-threaded execution"

These measurements become the motivation for **Phase 2** (async I/O) and **Phase 3** (multiprocessing).

---

## Getting Started Checklist

Before writing code, answer these planning questions:

1. What Python version are you using? (Should be 3.12+)
2. What will your `Message` structure look like? (dataclass? dict? NamedTuple?)
3. How many lines of code do you expect this to be? (Hint: Under 200 for core logic)
4. What external libraries will you need? (Hint: Maybe none! stdlib only for Phase 1)

---

## Ready to Start?

Begin with the message generator and get that working first. Then build the state machine. Then connect them.

When you get stuck or want to discuss design decisions, share:

1. What you tried
2. What behavior you observed
3. What you expected instead
4. Your hypothesis about why

I'll ask you guiding questions to help you discover the solution rather than just giving you the answer.

---

## First Question to Start Your Thinking

**If a message takes 5ms to process and you have 10 workers, what's the maximum throughput you could theoretically achieve in a single-threaded system? Why?**