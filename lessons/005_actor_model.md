# Phase 5: Cross-Worker Communication - "Actor Model"

## Learning Objectives

Understand:
- Actor model principles
- Message passing between workers
- Deadlock prevention strategies
- Causal consistency guarantees
- The coordinator pattern

## System Specification

### What You're Adding

Workers can now send updates to other workers:

```
Worker 1: State Machine → Update Message → Worker 2: State Machine
                                          ↓
                       Coordinator (routes cross-worker messages)
```

**Key change:** State machines can trigger updates in other state machines.

### Functional Requirements

**R1: Cross-Worker Updates**
- State machine can specify "notify these other workers" in its update
- Updates are delivered asynchronously (non-blocking)
- Updates carry: source worker_id, target worker_id(s), update payload

**R2: Update Queues**
- Each worker has separate queues for:
  - External messages (from source)
  - Cross-worker updates (from other workers)
- Updates should be processed with appropriate priority

**R3: Causal Ordering**
- If worker A sends update to B, then B sends to C, C should see correct order
- Implement vector clocks or lamport timestamps
- Queries should see causally consistent state

**R4: Deadlock Prevention**
- Workers cannot wait for each other
- No circular dependencies in update chains
- Bounded update chains (prevent infinite loops)

### Design Constraints

1. **No synchronous RPC** - all communication is async message passing
2. **No global locks** - each worker operates independently
3. **Fire-and-forget updates** - sending worker doesn't wait for ack
4. **Bounded queues** - updates can be dropped if queue full (backpressure)

## Architecture Guidance

### The Update Message

Define a new message type for cross-worker updates:

```python
@dataclass
class CrossWorkerUpdate:
    source_worker_id: str
    target_worker_id: str
    update_type: str  # "increment", "set", "compute", etc.
    payload: dict
    timestamp: float
    causal_clock: dict[str, int]  # Vector clock for causal ordering
```

**Design questions:**
1. How is this different from regular messages?
2. Should updates have priority over external messages?
3. How do you prevent infinite update loops?

### State Machine with Cross-Worker Capability

Extend state machines to generate updates:

```python
class StateMachine:
    def process_message(self, message: Message) -> list[CrossWorkerUpdate]:
        """Process message and return updates to send to other workers."""
        # Update own state
        self._update_internal_state(message)
        
        # Determine which other workers to notify
        updates = []
        if some_condition:
            updates.append(CrossWorkerUpdate(
                source_worker_id=self.worker_id,
                target_worker_id="other_worker",
                update_type="increment",
                payload={"key": "shared_counter"},
                timestamp=time.time(),
                causal_clock=self.vector_clock.copy()
            ))
        
        return updates
```

**Question:** How do you decide which workers to notify? Should this be:
- Hardcoded in state machine logic?
- Configured externally?
- Based on message content?

### The Coordinator

You need a component to route cross-worker updates:

```python
class UpdateCoordinator:
    def __init__(self, worker_registry: dict[str, mp.Queue]):
        self.worker_registry = worker_registry
        self.update_queue = mp.Queue(maxsize=10000)
    
    async def route_updates(self):
        """Continuously route updates from queue to target workers."""
        loop = asyncio.get_event_loop()
        
        while True:
            # Get update from coordinator queue
            update = await loop.run_in_executor(None, self.update_queue.get)
            
            # Route to target worker
            target_queue = self.worker_registry[update.target_worker_id]
            
            try:
                # Non-blocking put with immediate failure
                target_queue.put_nowait(update)
            except queue.Full:
                # Handle backpressure
                self._handle_dropped_update(update)
```

**Architecture question:** Should the coordinator be:
- A separate process?
- Part of the main process?
- Distributed across worker processes?

### Worker Process with Two Queues

Workers now consume from two sources:

```python
async def worker_main_loop(
    external_queue: mp.Queue,
    update_queue: mp.Queue,
    coordinator_queue: mp.Queue,
    state_machines: dict[str, StateMachine]
):
    loop = asyncio.get_event_loop()
    
    while True:
        # Create tasks for both queues
        external_task = loop.run_in_executor(None, external_queue.get)
        update_task = loop.run_in_executor(None, update_queue.get)
        
        # Wait for first to complete
        done, pending = await asyncio.wait(
            [external_task, update_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Process whichever arrived first
        for task in done:
            message = await task
            
            if isinstance(message, Message):
                # Regular message
                updates = process_message(message, state_machines)
                
                # Send updates to coordinator
                for update in updates:
                    coordinator_queue.put_nowait(update)
                    
            elif isinstance(message, CrossWorkerUpdate):
                # Cross-worker update
                apply_update(message, state_machines)
        
        # Cancel pending task
        for task in pending:
            task.cancel()
```

**Question:** Is this fair? Could one queue starve the other? How would you implement priority?

### Causal Consistency with Vector Clocks

Each worker maintains a vector clock:

```python
class VectorClock:
    def __init__(self, worker_id: str, all_worker_ids: list[str]):
        self.worker_id = worker_id
        self.clock: dict[str, int] = {wid: 0 for wid in all_worker_ids}
    
    def increment(self):
        """Increment own clock on local event."""
        self.clock[self.worker_id] += 1
    
    def update(self, other_clock: dict[str, int]):
        """Update clock on receiving message."""
        for worker_id, timestamp in other_clock.items():
            self.clock[worker_id] = max(self.clock[worker_id], timestamp)
        self.increment()
    
    def happens_before(self, other_clock: dict[str, int]) -> bool:
        """Check if this event happens before other."""
        return all(
            self.clock[wid] <= other_clock.get(wid, 0)
            for wid in self.clock
        )
```

**Use case:**
```python
# Worker A sends update
update.causal_clock = worker_a.vector_clock.copy()
worker_a.vector_clock.increment()

# Worker B receives update
worker_b.vector_clock.update(update.causal_clock)
```

**Question:** How do you use vector clocks to order concurrent updates?

### Deadlock Prevention

**Rule 1: No synchronous waits**
- Workers never block waiting for responses from other workers
- All communication is async, fire-and-forget

**Rule 2: Bounded update chains**
```python
@dataclass
class CrossWorkerUpdate:
    # ... other fields ...
    depth: int = 0  # Incremented each hop
    max_depth: int = 10  # Prevent infinite chains

def process_update(update: CrossWorkerUpdate) -> list[CrossWorkerUpdate]:
    if update.depth >= update.max_depth:
        logger.warning("Update chain too deep, dropping")
        return []
    
    # Process update and generate new updates
    new_updates = [...]
    for new_update in new_updates:
        new_update.depth = update.depth + 1
    
    return new_updates
```

**Question:** What's a reasonable max_depth? How do you choose it?

**Rule 3: Update DAG**
- Updates form a directed acyclic graph, not cycles
- Detect cycles and break them

### Backpressure for Updates

What happens when a worker can't keep up with updates?

**Strategy 1: Drop updates**
```python
try:
    target_queue.put_nowait(update)
except queue.Full:
    metrics.updates_dropped.inc()
    logger.warning("Dropped update", target=update.target_worker_id)
```

**Strategy 2: Priority queue**
- External messages have higher priority than updates
- Updates can be delayed but not dropped

**Strategy 3: Push back to source**
- Send NACK to source worker
- Source reduces update rate

**Question:** Which strategy makes sense for your use case?

## Experiments to Run

### Experiment 1: Update Propagation Latency

**Hypothesis:** Cross-worker updates add minimal latency.

**Test:**
1. Worker A sends update to worker B
2. Measure time from A sending → B processing
3. Compare to direct message processing latency

**Questions:**
- What's the overhead of routing through coordinator?
- How does this scale with number of hops (A→B→C→D)?

### Experiment 2: Causal Consistency Verification

**Test:**
1. Worker A sends update to B with vector clock [A:1, B:0]
2. Worker B sends update to C with vector clock [A:1, B:1, C:0]
3. Verify C never sees B's update before A's update

**Question:** How do you test that causal ordering is maintained?

### Experiment 3: Deadlock Scenarios

**Test:**
1. Create update cycle: A→B→C→A
2. Verify system doesn't deadlock
3. Measure: Are updates eventually dropped?

**Questions:**
- Does max_depth prevent cycles?
- What happens to system throughput with cycles?

### Experiment 4: Update Throughput

**Test:**
1. Measure external message throughput (no updates)
2. Measure throughput when 50% of messages generate updates
3. Measure throughput when every message generates 5 updates

**Questions:**
- How do updates affect external message throughput?
- What's the ratio of update bandwidth to message bandwidth?

### Experiment 5: Backpressure Behavior

**Test:**
1. Slow down one worker (add artificial delay)
2. Send updates to that worker from others
3. Measure: Queue depths, dropped updates, impact on senders

**Questions:**
- Does slow worker affect others?
- How well does backpressure contain the problem?

## Success Criteria

✅ **Cross-worker updates working:**
- Workers can send updates to other workers
- Updates are routed correctly
- No deadlocks observed

✅ **Causal consistency maintained:**
- Vector clocks implemented
- Updates processed in causal order
- Can verify consistency with tests

✅ **Performance acceptable:**
- Update latency < 10ms (p99)
- External message throughput not significantly impacted
- System stable under update-heavy workloads

✅ **Actor model understood:**
- Can explain actor principles
- Understand message passing vs shared state
- Know when to use which pattern

## Key Insights to Discover

1. **Async message passing is powerful** - Enables loose coupling
   - Workers are independent
   - Failures are isolated
   
2. **Ordering is hard** - Distributed systems have no global clock
   - Vector clocks provide partial order
   - Total ordering requires coordination (expensive)
   
3. **Backpressure cascades** - Slow workers affect others
   - Must design for graceful degradation
   - Dropping updates may be acceptable
   
4. **Cycles are dangerous** - Must be prevented or bounded
   - Max depth is simple but crude
   - Better: topological sorting or dependency tracking

## Questions to Guide Implementation

**Architecture:**
1. Should updates be reliable or can you tolerate drops?
2. How do you monitor update flow through the system?
3. What's your failure model for cross-worker communication?

**Implementation:**
1. How do you test causal consistency?
2. Should vector clocks be per-worker or per-state-machine?
3. How do you handle worker restart with lost clock state?

**Performance:**
1. What's the optimal queue size for update queues?
2. Should you batch updates like you batch external messages?
3. How do you prioritize between external messages and updates?

## Common Pitfalls

**Pitfall 1: Creating update cycles**
```python
# Worker A
if message.type == "increment":
    notify_worker_b()

# Worker B
if update.type == "increment":
    notify_worker_a()  # Infinite loop!
```

**Pitfall 2: Blocking on update send**
```python
# Wrong: Blocks until coordinator processes
coordinator_queue.put(update)  # Blocking!

# Right: Non-blocking with error handling
try:
    coordinator_queue.put_nowait(update)
except queue.Full:
    handle_backpressure()
```

**Pitfall 3: Ignoring vector clocks**
- Implementing vector clocks but not using them
- Processing updates out of order defeats the purpose

## Hints & Tips

**Hint 1: Testing causal consistency**
```python
# Record all updates with timestamps and clocks
updates_log = []

# After test, verify partial order
for i, update_a in enumerate(updates_log):
    for update_b in updates_log[i+1:]:
        if update_a.clock.happens_before(update_b.clock):
            assert update_a.processed_time < update_b.processed_time
```

**Hint 2: Visualizing update flow**
- Log all updates with graphviz DOT format
- Generate graph showing update propagation
- Useful for debugging cycles

**Hint 3: Coordinator as bottleneck**
- If coordinator is slow, consider sharding:
  - Multiple coordinator processes
  - Each handles a subset of workers
  - Use consistent hashing

## What's Next?

Phase 5 completes the core architecture but at a cost: serialization overhead still exists. Phase 6 tackles this with msgspec:
- 10x faster serialization
- Zero-copy deserialization for some types
- Structured schemas

This **motivates Phase 6**: Replacing pickle with msgspec throughout the system.
