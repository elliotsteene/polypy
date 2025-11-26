# Phase 6: Serialization Optimization - "msgspec"

## Learning Objectives

Understand:
- Why pickle is slow
- Zero-copy deserialization principles
- Schema evolution strategies
- Structured vs unstructured data tradeoffs
- The impact of serialization on end-to-end latency

## System Specification

### What You're Replacing

Replace all pickle-based serialization with msgspec:

```
Before: Python Object → pickle → bytes → unpickle → Python Object
After:  msgspec.Struct → msgpack → bytes → decode → msgspec.Struct
```

**Key change:** 10-50x faster serialization, lower memory allocations.

### Functional Requirements

**R1: Message Schema Definition**
- Define all message types as msgspec.Struct classes
- Include: Message, CrossWorkerUpdate, QueryRequest, QueryResponse
- Support nested structures and complex types

**R2: State Serialization**
- State machines serialize/deserialize state using msgspec
- Support custom types (e.g., numpy arrays, timestamps)
- Handle schema evolution (adding/removing fields)

**R3: Backwards Compatibility**
- System should handle mixed versions during deployment
- Old messages should be readable by new code
- New messages should degrade gracefully in old code

**R4: Performance Measurement**
- Benchmark before and after for:
  - Serialization time
  - Deserialization time
  - Memory allocations
  - End-to-end message latency

### Design Constraints

1. **Use msgspec for all serialization** - no pickle anywhere
2. **Type hints required** - msgspec uses annotations
3. **Immutable messages** - use `frozen=True` for Struct
4. **Reuse encoders/decoders** - create once, use many times

## Architecture Guidance

### Message Type Definitions

Define structured message types:

```python
import msgspec
from msgspec import Struct

# External message
class Message(Struct, frozen=True, kw_only=True):
    message_id: str
    worker_id: str
    payload: bytes
    timestamp: float
    priority: int = 0

# Cross-worker update
class CrossWorkerUpdate(Struct, frozen=True, kw_only=True):
    source_worker_id: str
    target_worker_id: str
    update_type: str
    payload: dict[str, msgspec.Raw]  # Allow flexible payload
    timestamp: float
    causal_clock: dict[str, int]
    depth: int = 0

# Query request/response
class QueryRequest(Struct, frozen=True, kw_only=True):
    query_id: str
    worker_id: str
    timeout: float = 1.0

class QueryResponse(Struct, frozen=True, kw_only=True):
    query_id: str
    worker_id: str
    state: bytes  # Serialized state
    timestamp: float
```

**Design questions:**
1. Should payload be `bytes`, `dict`, or `msgspec.Raw`?
2. How do you handle optional fields?
3. What about union types (message could be different types)?

### Encoder/Decoder Reuse

Critical for performance: reuse encoder/decoder instances:

```python
# Per-process global encoders/decoders
from msgspec import msgpack

_message_encoder = msgpack.Encoder()
_message_decoder = msgpack.Decoder(Message)

_update_encoder = msgpack.Encoder()
_update_decoder = msgpack.Decoder(CrossWorkerUpdate)

def encode_message(msg: Message) -> bytes:
    return _message_encoder.encode(msg)

def decode_message(data: bytes) -> Message:
    return _message_decoder.decode(data)
```

**Question:** Why is reuse important? Measure the difference!

### State Serialization Strategy

State can be complex. Options:

**Option 1: Simple dict serialization**
```python
state = {"counter": 42, "status": "active"}
state_bytes = msgpack.encode(state)
```

**Option 2: Structured state**
```python
class WorkerState(Struct):
    worker_id: str
    counter: int
    status: str
    last_updated: float
    metadata: dict[str, str]

state = WorkerState(...)
state_bytes = msgpack.encode(state)
```

**Question:** Which approach gives better performance? Why?

### Custom Type Handling

Some types need special handling:

**numpy arrays:**
```python
import numpy as np
from msgspec import Struct

class StateWithArray(Struct):
    data: bytes  # Serialize numpy as bytes
    shape: tuple[int, ...]
    dtype: str

def serialize_numpy(arr: np.ndarray) -> StateWithArray:
    return StateWithArray(
        data=arr.tobytes(),
        shape=arr.shape,
        dtype=str(arr.dtype)
    )

def deserialize_numpy(state: StateWithArray) -> np.ndarray:
    arr = np.frombuffer(state.data, dtype=state.dtype)
    return arr.reshape(state.shape)
```

**Question:** How does this compare to pickling numpy arrays?

### Schema Evolution

Handling field additions/removals:

```python
# Version 1
class Message(Struct):
    message_id: str
    worker_id: str
    payload: bytes

# Version 2 (add field with default)
class Message(Struct):
    message_id: str
    worker_id: str
    payload: bytes
    priority: int = 0  # New field, default value

# Version 3 (rename field - need migration)
class Message(Struct):
    message_id: str
    worker_id: str
    data: bytes  # Renamed from payload
    priority: int = 0
```

**Design challenge:** How do you handle v1 messages in v3 code?

**Solution ideas:**
- Version field in messages
- Try multiple decoders
- Migration layer

### Integration Points

Replace pickle throughout:

**1. Multiprocessing queues:**
```python
# Before
queue.put(message)  # Uses pickle internally

# After
queue.put(encode_message(message))  # Explicit encoding
# ... in worker ...
message = decode_message(queue.get())
```

**2. Shared memory:**
```python
# Already using bytes, just change what creates them
state_bytes = msgpack.encode(state)  # Instead of pickle.dumps()
shm.buf[8:8+len(state_bytes)] = state_bytes
```

**3. Network I/O (if applicable):**
```python
# Before
await writer.write(pickle.dumps(message))

# After
await writer.write(encode_message(message))
```

### Performance Comparison Framework

Create benchmarks to measure improvement:

```python
import time
import pickle
from msgspec import msgpack

def benchmark_serialization(obj, n_iterations=10000):
    # Pickle
    start = time.perf_counter()
    for _ in range(n_iterations):
        pickle.dumps(obj)
    pickle_time = time.perf_counter() - start
    
    # msgspec
    encoder = msgpack.Encoder()
    start = time.perf_counter()
    for _ in range(n_iterations):
        encoder.encode(obj)
    msgspec_time = time.perf_counter() - start
    
    print(f"Pickle: {pickle_time:.4f}s")
    print(f"msgspec: {msgspec_time:.4f}s")
    print(f"Speedup: {pickle_time/msgspec_time:.1f}x")
```

**Measure:**
- Simple messages (100 bytes)
- Complex messages (10 KB)
- State snapshots (100 KB)

## Experiments to Run

### Experiment 1: Serialization Performance

**Hypothesis:** msgspec is 10-50x faster than pickle.

**Test:**
1. Create messages of varying sizes: 100B, 1KB, 10KB, 100KB
2. Benchmark encode/decode time for both pickle and msgspec
3. Measure memory allocations (use memory_profiler)

**Questions:**
- At what size does msgspec advantage increase?
- Where does the speedup come from?
- How much memory is saved?

### Experiment 2: End-to-End Latency Improvement

**Hypothesis:** Faster serialization reduces end-to-end latency.

**Test:**
1. Run full system with pickle (Phase 5)
2. Run full system with msgspec (Phase 6)
3. Measure p50, p95, p99 latencies

**Questions:**
- What percentage of latency was serialization?
- Does throughput increase?
- At what message rate do you see improvement?

### Experiment 3: Queue Throughput

**Test:**
1. Measure maximum throughput through multiprocessing.Queue
2. Vary message size
3. Compare pickle vs msgspec

**Questions:**
- Does msgspec increase queue throughput?
- Is serialization the bottleneck or the queue itself?

### Experiment 4: Shared Memory Write Frequency

**Hypothesis:** Faster serialization allows more frequent state syncs.

**Test:**
1. Measure CPU overhead of state sync at different intervals
2. Compare pickle vs msgspec
3. Find optimal sync interval for each

**Questions:**
- Can you sync 10x more frequently with msgspec?
- Does this reduce query staleness?
- What's the CPU cost?

### Experiment 5: Schema Evolution

**Test:**
1. Create v1 messages
2. Update schema to v2 (add field)
3. Verify v2 code can read v1 messages
4. Measure performance overhead of migration

**Questions:**
- How do you detect schema version?
- What's the performance cost of handling multiple versions?

## Success Criteria

✅ **Complete migration:**
- All pickle usage replaced with msgspec
- All message types are Struct classes
- Type hints on all serialized data

✅ **Performance improvements achieved:**
- 10x faster serialization (minimum)
- Reduced memory allocations
- Measurable end-to-end latency improvement

✅ **Schema evolution working:**
- Can handle field additions with defaults
- Version detection implemented
- Backwards compatibility verified

✅ **Deep understanding:**
- Can explain why msgspec is faster
- Understand zero-copy deserialization
- Know tradeoffs of structured schemas

## Key Insights to Discover

1. **Serialization is often the bottleneck** - But hidden
   - Profile shows significant time in pickle
   - Switching to msgspec gives dramatic improvement
   
2. **Structured schemas have benefits** - Beyond performance
   - Type safety catches bugs
   - Schema evolution is explicit
   - Documentation is self-contained
   
3. **Zero-copy is not always zero-copy** - Context matters
   - msgspec can avoid copies for some types
   - But multiprocessing.Queue still copies
   - Shared memory is truly zero-copy
   
4. **Reusing encoder/decoder matters** - Initialization cost
   - Creating encoder per message is expensive
   - Global reuse gives best performance

## Questions to Guide Implementation

**Planning:**
1. Which message types to migrate first?
2. How to verify correctness after migration?
3. What's your rollback plan if something breaks?

**Implementation:**
1. How do you handle complex nested types?
2. Should you use msgpack or json encoding?
3. What about custom types not supported by msgspec?

**Validation:**
1. How do you test that migration is correct?
2. What if you missed a pickle call somewhere?
3. How do you verify performance improvement?

## Common Pitfalls

**Pitfall 1: Forgetting to reuse encoder/decoder**
```python
# Slow: Creates new encoder each time
def encode(obj):
    return msgpack.Encoder().encode(obj)

# Fast: Reuses global encoder
_encoder = msgpack.Encoder()
def encode(obj):
    return _encoder.encode(obj)
```

**Pitfall 2: Using wrong Struct options**
```python
# Mutable struct (slower)
class Message(Struct):
    ...

# Immutable struct (faster, safer)
class Message(Struct, frozen=True):
    ...
```

**Pitfall 3: Not handling None/optional**
```python
# Will fail if field is None
class Message(Struct):
    value: int  # Can't be None

# Correct
class Message(Struct):
    value: int | None = None
```

## Hints & Tips

**Hint 1: Benchmarking serialization**
```python
# Warm up first
for _ in range(100):
    encoder.encode(obj)

# Then measure
times = []
for _ in range(10000):
    start = time.perf_counter_ns()
    encoder.encode(obj)
    times.append(time.perf_counter_ns() - start)

print(f"Median: {statistics.median(times)/1000:.1f} µs")
print(f"P95: {statistics.quantiles(times, n=20)[18]/1000:.1f} µs")
```

**Hint 2: msgpack vs json**
```python
# msgpack: binary, faster, smaller
msgpack_encoder = msgpack.Encoder()
msgpack_bytes = msgpack_encoder.encode(obj)

# json: text, human-readable, debuggable
json_encoder = json.Encoder()
json_bytes = json_encoder.encode(obj)

# Compare sizes
print(f"msgpack: {len(msgpack_bytes)} bytes")
print(f"json: {len(json_bytes)} bytes")
```

**Hint 3: Struct validation**
```python
# msgspec validates types automatically
@dataclass
class Message(Struct):
    value: int

msg = Message(value="not an int")  # Raises ValidationError!
```

## What's Next?

Phase 6 dramatically improves serialization performance but doesn't address system-level concerns like backpressure and stability. Phase 7 adds adaptive flow control:
- Dynamic queue sizing
- Circuit breakers
- Health checks
- Graceful degradation

This **motivates Phase 7**: Advanced flow control for production stability.
