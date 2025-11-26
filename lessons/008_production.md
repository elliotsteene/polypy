# Phase 8: Production Hardening - "Observability & Recovery"

## Learning Objectives

Understand:
- Production-ready vs working code differences
- Observability pillars: metrics, logs, traces
- Event sourcing for replay and debugging
- Graceful shutdown patterns
- Failure recovery strategies

## System Specification

### What You're Adding

Transform working system into production-ready system:

```
All Components →  Structured Logging
                  Prometheus Metrics
                  OpenTelemetry Traces
                  Event Store
                  Health Endpoints
                  Graceful Shutdown
```

**Key change:** System is observable, debuggable, and recoverable.

### Functional Requirements

**R1: Comprehensive Metrics**
- Expose all key metrics via Prometheus endpoint
- Include: throughput, latency, queue depth, error rate, resource usage
- Per-worker metrics and system-wide aggregates

**R2: Structured Logging**
- All logs in JSON format with structured fields
- Include: timestamp, level, component, worker_id, message_id, context
- Correlation IDs for tracing requests across components

**R3: Event Sourcing**
- All state transitions recorded as events
- Events persisted to append-only log
- Ability to replay events to reconstruct state

**R4: Health Endpoints**
- HTTP endpoint for liveness check (is process alive?)
- HTTP endpoint for readiness check (can it handle requests?)
- Detailed health status with component breakdown

**R5: Graceful Shutdown**
- Handle SIGTERM/SIGINT signals
- Drain queues before stopping
- Close all resources cleanly
- Timeout and force-kill if necessary

**R6: Recovery Mechanisms**
- Automatic worker restart on crash
- State recovery from event log
- Checkpoint/restore for fast recovery

### Design Constraints

1. **Zero impact on performance** - observability shouldn't slow system
2. **Async logging** - don't block on log writes
3. **Sampling** - trace subset of requests, not all
4. **Retention policies** - don't fill disk with logs/events

## Architecture Guidance

### Structured Logging with structlog

Replace print statements with structured logs:

```python
import structlog
import logging

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Usage
logger.info("message_processed", 
           message_id=msg.message_id,
           worker_id=msg.worker_id,
           latency_ms=latency,
           queue_depth=queue.qsize())
```

**Design challenge:** Add context to logger for automatic inclusion:

```python
# Bind context to logger
logger = logger.bind(worker_id="worker_1", component="processor")

# All subsequent logs include context
logger.info("processing_message")  # Automatically includes worker_id
```

### Prometheus Metrics

Expose metrics for monitoring:

```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Define metrics
messages_received = Counter(
    'messages_received_total',
    'Total messages received',
    ['worker_type']
)

messages_processed = Counter(
    'messages_processed_total',
    'Total messages processed',
    ['worker_id', 'status']
)

processing_duration = Histogram(
    'message_processing_duration_seconds',
    'Message processing duration',
    ['worker_id'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

queue_depth = Gauge(
    'queue_depth',
    'Current queue depth',
    ['worker_id']
)

# Usage
messages_received.labels(worker_type='trading').inc()

with processing_duration.labels(worker_id='worker_1').time():
    process_message(msg)

queue_depth.labels(worker_id='worker_1').set(current_depth)

# Start metrics server
start_http_server(9090)
```

**Question:** What metrics are most important? Think about:
- RED metrics: Rate, Errors, Duration
- USE metrics: Utilization, Saturation, Errors
- System metrics: CPU, memory, network

### Event Sourcing

Record all state changes:

```python
@dataclass
class StateTransitionEvent:
    event_id: str
    worker_id: str
    timestamp: float
    event_type: str  # "message_processed", "update_applied", etc.
    from_state: dict
    to_state: dict
    metadata: dict

class EventStore:
    """
    Append-only event log for state transitions.
    """
    
    def __init__(self, log_path: str):
        self.log_path = log_path
        self.log_file = open(log_path, 'ab')
        self.encoder = msgpack.Encoder()
    
    async def append_event(self, event: StateTransitionEvent):
        """Append event to log."""
        event_bytes = self.encoder.encode(event)
        
        # Write length-prefixed record
        length = len(event_bytes)
        record = length.to_bytes(4, 'little') + event_bytes
        
        # Async write (use aiofiles for true async)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.log_file.write, record)
        await loop.run_in_executor(None, self.log_file.flush)
    
    def replay_events(self, worker_id: str) -> list[StateTransitionEvent]:
        """Replay events for a worker to reconstruct state."""
        events = []
        
        with open(self.log_path, 'rb') as f:
            decoder = msgpack.Decoder(StateTransitionEvent)
            
            while True:
                # Read length prefix
                length_bytes = f.read(4)
                if not length_bytes:
                    break
                
                length = int.from_bytes(length_bytes, 'little')
                event_bytes = f.read(length)
                
                event = decoder.decode(event_bytes)
                if event.worker_id == worker_id:
                    events.append(event)
        
        return events
```

**Design questions:**
1. Should you log every state change or sample?
2. How do you handle log rotation?
3. What's the retention policy?

### Health Endpoints with FastAPI

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class HealthStatus(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    components: dict[str, str]
    metrics: dict[str, float]

@app.get("/health/live")
async def liveness():
    """Liveness check: is the process alive?"""
    return {"status": "ok"}

@app.get("/health/ready")
async def readiness():
    """Readiness check: can the process handle requests?"""
    # Check if all workers are healthy
    if all_workers_healthy():
        return {"status": "ready"}
    else:
        return {"status": "not_ready"}, 503

@app.get("/health/status")
async def detailed_status():
    """Detailed health status."""
    return HealthStatus(
        status="healthy",
        components={
            "ingestion": "healthy",
            "workers": "healthy",
            "coordinator": "healthy",
        },
        metrics={
            "messages_per_second": get_throughput(),
            "avg_latency_ms": get_avg_latency(),
            "queue_depth": get_queue_depth(),
        }
    )
```

### Graceful Shutdown

Handle shutdown signals properly:

```python
import signal
import asyncio

class SystemOrchestrator:
    def __init__(self):
        self._shutdown_event = asyncio.Event()
        self._components = []
    
    async def start(self):
        """Start system with signal handlers."""
        loop = asyncio.get_event_loop()
        
        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown())
            )
        
        logger.info("system_starting")
        
        try:
            # Start all components
            async with asyncio.TaskGroup() as tg:
                for component in self._components:
                    tg.create_task(component.start())
                
                # Wait for shutdown signal
                await self._shutdown_event.wait()
                
        except* Exception as eg:
            logger.error("system_error", errors=[str(e) for e in eg.exceptions])
    
    async def shutdown(self):
        """Gracefully shutdown system."""
        logger.info("shutdown_initiated")
        
        # Phase 1: Stop accepting new messages
        await self._stop_ingestion()
        
        # Phase 2: Drain queues (with timeout)
        await self._drain_queues(timeout=30.0)
        
        # Phase 3: Stop workers
        await self._stop_workers()
        
        # Phase 4: Cleanup resources
        await self._cleanup()
        
        # Signal shutdown complete
        self._shutdown_event.set()
        logger.info("shutdown_complete")
    
    async def _stop_ingestion(self):
        """Stop accepting new messages."""
        logger.info("stopping_ingestion")
        self.ingestion.stop()
    
    async def _drain_queues(self, timeout: float):
        """Wait for queues to drain."""
        logger.info("draining_queues")
        start = time.time()
        
        while time.time() - start < timeout:
            if all_queues_empty():
                logger.info("queues_drained")
                return
            await asyncio.sleep(1.0)
        
        logger.warning("queue_drain_timeout", remaining=get_queue_depths())
    
    async def _stop_workers(self):
        """Stop all worker processes."""
        logger.info("stopping_workers")
        await self.worker_registry.shutdown()
    
    async def _cleanup(self):
        """Cleanup resources."""
        logger.info("cleanup_started")
        # Close event store
        # Unlink shared memory
        # Close network connections
        # etc.
```

### Recovery from Crash

Implement checkpoint/restore:

```python
class Checkpoint:
    """
    Periodic state checkpointing for fast recovery.
    """
    
    def __init__(self, checkpoint_dir: str, interval: float = 60.0):
        self.checkpoint_dir = checkpoint_dir
        self.interval = interval
    
    async def checkpoint_loop(self, registry: WorkerRegistry):
        """Periodically checkpoint all worker states."""
        while True:
            await asyncio.sleep(self.interval)
            
            logger.info("checkpoint_started")
            checkpoint_time = time.time()
            
            # Collect states from all workers
            states = {}
            for worker_id, worker in registry.workers.items():
                try:
                    state = await worker.get_state()
                    states[worker_id] = state
                except Exception as e:
                    logger.error("checkpoint_failed", worker_id=worker_id, error=str(e))
            
            # Write checkpoint
            checkpoint_file = f"{self.checkpoint_dir}/checkpoint_{checkpoint_time}.msgpack"
            async with aiofiles.open(checkpoint_file, 'wb') as f:
                await f.write(msgpack.encode(states))
            
            logger.info("checkpoint_complete", num_workers=len(states))
    
    async def restore_from_checkpoint(self) -> dict[str, dict]:
        """Restore state from latest checkpoint."""
        # Find latest checkpoint
        checkpoints = sorted(Path(self.checkpoint_dir).glob("checkpoint_*.msgpack"))
        if not checkpoints:
            logger.info("no_checkpoint_found")
            return {}
        
        latest = checkpoints[-1]
        logger.info("restoring_from_checkpoint", file=str(latest))
        
        async with aiofiles.open(latest, 'rb') as f:
            data = await f.read()
            return msgpack.decode(data)
```

### Distributed Tracing (Optional/Advanced)

If you want to trace requests across components:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger import JaegerExporter

# Setup tracing
trace.set_tracer_provider(TracerProvider())
jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",
    agent_port=6831,
)
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(jaeger_exporter)
)

tracer = trace.get_tracer(__name__)

# Usage
with tracer.start_as_current_span("process_message") as span:
    span.set_attribute("message_id", msg.message_id)
    span.set_attribute("worker_id", msg.worker_id)
    
    process_message(msg)
    
    span.set_attribute("latency_ms", latency)
```

## Experiments to Run

### Experiment 1: Observability Overhead

**Hypothesis:** Observability adds <5% overhead.

**Test:**
1. Measure throughput without metrics/logging
2. Add structured logging
3. Add Prometheus metrics
4. Add tracing (if implemented)
5. Measure overhead at each step

**Questions:**
- Which adds most overhead?
- Is overhead acceptable?
- Can you reduce it with sampling?

### Experiment 2: Event Replay Correctness

**Test:**
1. Run system and process 10,000 messages
2. Stop system and clear all state
3. Replay events from event log
4. Compare replayed state to original state

**Expected:** State should be identical after replay.

### Experiment 3: Graceful Shutdown Timing

**Test:**
1. Send messages at 80% capacity
2. Send SIGTERM
3. Measure:
   - Time to stop accepting new messages
   - Time to drain queues
   - Total shutdown time
   - Messages lost (should be 0)

**Questions:**
- Is shutdown time acceptable?
- What determines drain time?
- How do you optimize?

### Experiment 4: Crash Recovery

**Test:**
1. Process messages normally
2. Kill worker process (SIGKILL)
3. Restart worker
4. Verify state recovery
5. Measure recovery time

**Questions:**
- How much state is lost?
- How long does recovery take?
- Can you improve recovery time?

### Experiment 5: Health Check Accuracy

**Test:**
1. Introduce various failures:
   - Slow worker
   - High error rate
   - Full queue
2. Verify health endpoint reflects issues
3. Measure detection latency

**Questions:**
- How quickly are issues detected?
- Are there false positives/negatives?
- Is health check granular enough?

## Success Criteria

✅ **Fully observable:**
- Structured logs with context
- Comprehensive Prometheus metrics
- Health endpoints working
- Can diagnose issues from logs/metrics alone

✅ **Resilient:**
- Graceful shutdown implemented
- Event sourcing working
- Checkpoint/restore functional
- System recovers from crashes

✅ **Production-ready:**
- No resource leaks
- Proper error handling everywhere
- Documentation complete
- Deployment automation

✅ **Maintainable:**
- Code is clean and well-organized
- Tests cover critical paths
- Observability aids debugging
- System is understandable

## Key Insights to Discover

1. **Observability is not optional** - Can't debug without it
   - Structured logs enable searching
   - Metrics enable alerting
   - Traces show request flow
   
2. **Graceful shutdown is hard** - Many edge cases
   - In-flight requests
   - Drain timeouts
   - Resource cleanup ordering
   
3. **Event sourcing is powerful** - But has costs
   - Complete audit trail
   - Enables replay for debugging
   - But storage and performance overhead
   
4. **Health checks need nuance** - Not just up/down
   - Liveness vs readiness
   - Component-level health
   - Gradual degradation

## Questions to Guide Implementation

**Planning:**
1. What metrics are critical vs nice-to-have?
2. How much event history to retain?
3. What's acceptable shutdown time?

**Implementation:**
1. Should logging be synchronous or async?
2. Where to export metrics to? (Prometheus, Datadog, etc.)
3. How to test graceful shutdown?

**Operations:**
1. How do you monitor the system in production?
2. What alerts should you set up?
3. How do you debug a production issue?

## Common Pitfalls

**Pitfall 1: Synchronous logging**
```python
# Blocks event loop
logger.info(...)  # Writes to disk synchronously

# Better: Use async logging handler
```

**Pitfall 2: High cardinality metrics**
```python
# Bad: Creates infinite unique metrics
latency.labels(message_id=msg.message_id)

# Good: Low cardinality
latency.labels(worker_id=msg.worker_id)
```

**Pitfall 3: No log sampling**
```python
# Logs every message (millions/hour)
logger.info("message_processed", ...)

# Better: Sample
if random.random() < 0.01:  # 1% sample rate
    logger.info("message_processed", ...)
```

## Hints & Tips

**Hint 1: Testing shutdown**
```python
# Simulate signal in test
import signal
os.kill(os.getpid(), signal.SIGTERM)
await asyncio.sleep(1)  # Let shutdown proceed
assert system.is_stopped()
```

**Hint 2: Log aggregation**
```python
# Use consistent field names for easier querying
logger.info(
    "event_name",
    message_id=...,  # Always "message_id", not "msg_id"
    worker_id=...,   # Always "worker_id", not "worker"
    timestamp=...,   # Always include timestamp
)
```

**Hint 3: Metric naming**
```python
# Follow Prometheus naming conventions
# - Use base unit (seconds, not milliseconds)
# - Include unit in name
# - Use _total suffix for counters

processing_duration_seconds  # Good
processing_time_ms          # Bad
```

## Final Deliverable

You've now built a complete, production-ready concurrent state management system with:

- ✅ High-throughput message ingestion
- ✅ Process-based parallelism (defeating GIL)
- ✅ Zero-copy state queries (shared memory)
- ✅ Cross-worker communication (actor model)
- ✅ Optimized serialization (msgspec)
- ✅ Adaptive flow control (backpressure)
- ✅ Full observability (metrics, logs, traces)
- ✅ Production hardening (graceful shutdown, recovery)

**Congratulations!** You've gone from a simple blocking system to a sophisticated, production-ready distributed system.

## Next Steps Beyond This Path

**Further learning:**
1. **Distributed deployment** - Run across multiple machines
2. **Kubernetes integration** - Deploy on K8s with proper resources
3. **Advanced patterns** - CQRS, saga pattern, event streaming
4. **Performance tuning** - Profile and optimize hot paths
5. **Chaos engineering** - Test failure scenarios systematically

**Production considerations:**
1. Security: Authentication, authorization, encryption
2. Compliance: Audit logs, data retention, GDPR
3. Cost optimization: Resource usage, cloud costs
4. Capacity planning: Growth projections, scaling limits

---



**Total estimated time: 54-85 hours** (7-11 days of focused work)
