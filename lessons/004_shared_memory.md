# Phase 4: Zero-Copy State Access - "Shared Memory"

## Learning Objectives

Understand:
- Shared memory primitives in Python
- Zero-copy patterns
- Lock-free data structures
- Memory-mapped I/O
- The reader/writer problem

## System Specification

### What You're Adding

Workers write state to shared memory; main process reads without serialization:

```
Worker Process 1: State Machine → Shared Memory Segment 1
Worker Process 2: State Machine → Shared Memory Segment 2
                                        ↓ (zero-copy read)
Main Process: Query Interface → Read from Shared Memory
```

**Key change:** State queries no longer require IPC round-trip.

### Functional Requirements

**R1: Shared Memory State Buffer**
- Each worker maintains state in shared memory
- State is serialized once when updated
- Queries read directly from shared memory (no IPC)

**R2: Double-Buffering**
- Implement double-buffering to prevent read/write conflicts
- Readers see consistent snapshots
- Writers never block readers

**R3: Dynamic Resizing**
- Shared memory buffers can grow if state exceeds initial size
- Handle resizing without breaking readers

**R4: Query Performance**
- Query latency should drop to < 100μs
- Queries should not involve any IPC

### Design Constraints

1. **Use `multiprocessing.shared_memory` (Python 3.8+)**
2. **No locks for reads** - implement lock-free reading
3. **Atomic writes** - readers never see partial updates
4. **Memory-efficient** - reuse buffers, don't leak

## Architecture Guidance

### Shared Memory Basics

Python's shared memory module:

```python
from multiprocessing import shared_memory

# Create shared memory segment
shm = shared_memory.SharedMemory(
    name="worker_1_state",
    create=True,
    size=1024 * 1024  # 1 MB
)

# Write to it
shm.buf[0:100] = b"some data..."

# Read from another process
shm2 = shared_memory.SharedMemory(name="worker_1_state")
data = bytes(shm2.buf[0:100])
```

**Critical questions:**
1. How do you know how much data is actually in the buffer?
2. What if you read while it's being written?
3. How do you clean up shared memory segments?

### The Metadata Problem

You need to store:
- Size of actual data in buffer
- Version/sequence number (to detect updates)
- Possibly a checksum

**Design challenge:** Store metadata alongside data without complex formats.

**Simple approach:**
```
[0:4]   → data size (4 bytes, little-endian)
[4:8]   → sequence number (4 bytes, little-endian)
[8:N]   → actual data
```

```python
def write_to_shared_memory(shm: shared_memory.SharedMemory, 
                          data: bytes, 
                          sequence: int) -> None:
    size = len(data)
    shm.buf[0:4] = size.to_bytes(4, 'little')
    shm.buf[4:8] = sequence.to_bytes(4, 'little')
    shm.buf[8:8+size] = data
```

**Question:** Is this atomic? What if the reader reads between the size write and data write?

### Double-Buffering Pattern

To achieve lock-free reads, use two buffers:

```python
class DoubleBuffer:
    def __init__(self, name: str, size: int):
        self.buffer_a = shared_memory.SharedMemory(
            name=f"{name}_a", create=True, size=size
        )
        self.buffer_b = shared_memory.SharedMemory(
            name=f"{name}_b", create=True, size=size
        )
        # Single byte for active buffer index (0 or 1)
        self.active = shared_memory.SharedMemory(
            name=f"{name}_active", create=True, size=1
        )
        self.active.buf[0] = 0  # Start with buffer A
    
    def write(self, data: bytes) -> None:
        """Write to inactive buffer, then swap."""
        current = self.active.buf[0]
        inactive_buffer = self.buffer_b if current == 0 else self.buffer_a
        
        # Write to inactive buffer
        # ... write data ...
        
        # Atomic swap (single byte write is atomic)
        self.active.buf[0] = 1 - current
    
    def read(self) -> bytes:
        """Read from active buffer."""
        current = self.active.buf[0]
        active_buffer = self.buffer_a if current == 0 else self.buffer_b
        
        # Read from active buffer
        # ... read data ...
```

**Question:** Is swapping the active buffer truly atomic? Why does single-byte write matter?

**Design challenge:** What if the writer updates very frequently? Could a reader see the same buffer twice?

### Integration with Worker Process

Workers need to sync state to shared memory periodically:

```python
async def state_sync_loop(state_machine: StateMachine, 
                         shared_buffer: DoubleBuffer):
    """Periodically sync state to shared memory."""
    sequence = 0
    
    while True:
        await asyncio.sleep(0.1)  # Sync every 100ms
        
        # Serialize current state
        state_bytes = serialize_state(state_machine.get_state())
        
        # Write to shared memory
        shared_buffer.write(state_bytes, sequence)
        sequence += 1
```

**Critical questions:**
1. What's the right sync interval? Too frequent → wasted CPU, too slow → stale queries
2. Should you sync after every state change or on a timer?
3. What if serialization takes longer than the sync interval?

### Query Implementation

Queries now become trivial:

```python
class SharedMemoryQueryService:
    def __init__(self, worker_registry: dict[str, DoubleBuffer]):
        self.worker_registry = worker_registry
    
    def query_state(self, worker_id: str) -> dict:
        """Zero-copy state query."""
        # 1. Determine which shared memory segment
        buffer = self.worker_registry[worker_id]
        
        # 2. Read from shared memory
        state_bytes = buffer.read()
        
        # 3. Deserialize
        return deserialize_state(state_bytes)
```

**Measure this:** Query latency should drop from milliseconds to microseconds.

### Memory Management

Shared memory segments persist even after processes exit. You MUST clean them up:

```python
def cleanup_shared_memory():
    """Called on shutdown."""
    for buffer in all_buffers:
        buffer.buffer_a.close()
        buffer.buffer_a.unlink()  # Actually delete the segment
        buffer.buffer_b.close()
        buffer.buffer_b.unlink()
        buffer.active.close()
        buffer.active.unlink()
```

**Pitfall:** Forgotten `unlink()` causes shared memory leaks in `/dev/shm`.

### Dynamic Resizing

What if state grows beyond initial buffer size?

**Strategy 1: Pre-allocate large buffers**
- Simple but wasteful

**Strategy 2: Resize on demand**
```python
def resize_buffer(old_buffer: shared_memory.SharedMemory, 
                 new_size: int) -> shared_memory.SharedMemory:
    """Create new buffer, copy data, delete old."""
    new_buffer = shared_memory.SharedMemory(
        name=f"{old_buffer.name}_v2",
        create=True,
        size=new_size
    )
    
    # Copy data from old to new
    data_size = int.from_bytes(old_buffer.buf[0:4], 'little')
    new_buffer.buf[0:data_size+8] = old_buffer.buf[0:data_size+8]
    
    # Clean up old buffer
    old_buffer.close()
    old_buffer.unlink()
    
    return new_buffer
```

**Problem:** How do readers know the buffer was resized? They have the old name!

**Solution ideas:**
- Indirect naming: lookup table of worker_id → current buffer name
- Version in shared metadata
- Reader retries on read failure

## Experiments to Run

### Experiment 1: Query Latency Improvement

**Hypothesis:** Shared memory reduces query latency by 100x.

**Test:**
1. Measure query latency in Phase 3 (IPC-based)
2. Measure query latency in Phase 4 (shared memory)
3. Break down latency: find overhead, deserialization, etc.

**Target:** < 100μs for p99 query latency

### Experiment 2: Read/Write Interference

**Hypothesis:** Readers don't interfere with writers using double-buffering.

**Test:**
1. Writer updates state at high frequency (every 1ms)
2. Reader queries at high frequency (1000/second)
3. Measure: Does read latency change with write frequency?

**Questions:**
- Do you ever see torn reads (partial updates)?
- Does write throughput decrease with concurrent readers?

### Experiment 3: Sync Interval Tuning

**Test:**
1. Vary sync interval: 1ms, 10ms, 100ms, 1000ms
2. Measure:
   - CPU usage
   - Query staleness (time since last update)
   - Impact on message processing throughput

**Questions:**
- What's the optimal sync interval?
- How do you balance freshness vs overhead?

### Experiment 4: Memory Overhead

**Test:**
1. Create 1000 workers with shared memory
2. Measure total memory usage
3. Compare to Phase 3 (queue-based queries)

**Questions:**
- How much memory per worker?
- How does this compare to queue-based approach?
- What's the memory efficiency?

### Experiment 5: Serialization Format Comparison

**Test:**
1. Compare serialization formats: pickle, msgpack, json, custom binary
2. Measure:
   - Serialization time
   - Deserialization time
   - Serialized size
   
**Questions:**
- Which format is fastest?
- Which produces smallest output?
- Tradeoffs?

## Success Criteria

✅ **Zero-copy reads working:**
- Queries read from shared memory without IPC
- No serialization in the read path (until deserialization in main process)
- Lock-free reading implemented

✅ **Performance targets met:**
- Query latency < 100μs (p99)
- No interference between reads and writes
- CPU overhead of syncing is minimal

✅ **Memory managed correctly:**
- No shared memory leaks
- Clean shutdown removes all segments
- Dynamic resizing works (if implemented)

✅ **Double-buffering understood:**
- Can explain why it prevents torn reads
- Know when it's necessary vs overkill
- Understand the atomic swap pattern

## Key Insights to Discover

1. **Zero-copy is transformative** - Latency drops dramatically
   - No serialization in hot path
   - No IPC overhead
   
2. **Lock-free is possible but subtle** - Careful design required
   - Single-byte writes are atomic
   - Double-buffering prevents races
   - But still need sequence numbers to detect staleness
   
3. **Memory management is manual** - Unlike Python objects
   - Must explicitly unlink shared memory
   - Easy to leak resources
   
4. **Tradeoffs exist** - Not a silver bullet
   - State must be serialized periodically
   - Memory usage increases
   - Complexity increases

## Questions to Guide Implementation

**Before implementing:**
1. How large should initial buffers be?
2. What serialization format will you use?
3. How will you handle buffer naming and lookup?

**While implementing:**
1. How do you test for torn reads?
2. What happens if deserialization fails?
3. Should you add checksums to detect corruption?

**After implementing:**
1. Can you prove that reads never block writes?
2. How stale can queries be?
3. What's the CPU cost of continuous syncing?

## Hints & Tips

**Hint 1: Testing Double-Buffering**
```python
# Stress test: rapid writes and reads
async def stress_test():
    writer_task = asyncio.create_task(rapid_writes())
    reader_task = asyncio.create_task(rapid_reads())
    
    await asyncio.gather(writer_task, reader_task)
    
    # Check: Did any read fail? Any torn reads?
```

**Hint 2: Debugging Shared Memory**
```bash
# List shared memory segments
ls -lh /dev/shm/

# Check for leaks
lsof /dev/shm/
```

**Hint 3: Sequence Number Usage**
```python
def read_with_retry(buffer: DoubleBuffer, max_retries=3):
    for _ in range(max_retries):
        seq1 = read_sequence(buffer)
        data = read_data(buffer)
        seq2 = read_sequence(buffer)
        
        if seq1 == seq2:
            return data  # Consistent read
    
    raise ValueError("Could not get consistent read")
```

## What's Next?

Phase 4 dramatically improves query performance but state updates still go through IPC. Phase 5 adds cross-worker communication, introducing new challenges:
- How do workers update each other's state?
- How do you prevent deadlocks?
- How do you maintain causal ordering?

This **motivates Phase 5**: Actor model for cross-worker updates.
