# Phase 7: Advanced Flow Control - "Backpressure & Stability"

## Learning Objectives

Understand:
- Backpressure as a system-wide concern
- Adaptive algorithms for resource management
- Circuit breaker patterns
- Queue theory and Little's Law
- Graceful degradation strategies

## System Specification

### What You're Adding

Transform fixed queues into adaptive, self-regulating system:

```
Message Source → [Adaptive Queue] → Workers → [Adaptive Queue] → Coordinator
                      ↓                           ↓
                  Backpressure                  Backpressure
                  Monitoring                    Monitoring
                      ↓                           ↓
                  Dynamic Sizing               Circuit Breaker
```

**Key change:** System automatically adjusts to load and prevents overload.

### Functional Requirements

**R1: Adaptive Queue Sizing**
- Queues grow/shrink based on observed latency
- Target: Keep queue wait time below threshold (e.g., 100ms)
- Min/max bounds to prevent pathological cases

**R2: Circuit Breakers**
- Detect when workers are unhealthy (high error rate, high latency)
- Temporarily stop sending messages to failing workers
- Automatic recovery attempts

**R3: Health Checks**
- Each worker reports health metrics:
  - Queue depth
  - Processing latency
  - Error rate
  - CPU/memory usage
- Main process aggregates and acts on metrics

**R4: Load Shedding**
- When system is overloaded, intelligently drop messages
- Priority-based dropping (low priority first)
- Fair dropping across workers

**R5: Graceful Degradation**
- System remains available even under extreme load
- Reduced functionality better than complete failure
- User-facing error messages when dropping requests

### Design Constraints

1. **No manual tuning** - system self-adjusts based on metrics
2. **Responsive** - react to changes within seconds, not minutes
3. **Stable** - avoid oscillation or thrashing
4. **Observable** - expose all control decisions for debugging

## Architecture Guidance

### Adaptive Queue

Core concept: Queue size adapts to processing latency.

```python
from collections import deque
import asyncio
from dataclasses import dataclass, field

@dataclass
class AdaptiveQueueMetrics:
    """Metrics for adaptive queue sizing."""
    total_enqueued: int = 0
    total_dequeued: int = 0
    current_depth: int = 0
    avg_wait_time: float = 0.0
    processing_latency_p95: float = 0.0

class AdaptiveQueue:
    """
    Queue that adapts size based on observed latency.
    
    Algorithm:
    - If avg_wait_time > target: increase size
    - If avg_wait_time < target/2: decrease size
    - Update every N messages or T seconds
    """
    
    def __init__(
        self,
        initial_size: int = 1000,
        min_size: int = 100,
        max_size: int = 100000,
        target_wait_ms: float = 100.0,
        adjustment_interval: float = 10.0,  # seconds
    ):
        self._queue: deque = deque()
        self._size = initial_size
        self._min_size = min_size
        self._max_size = max_size
        self._target_wait_ms = target_wait_ms
        self._adjustment_interval = adjustment_interval
        
        # Metrics
        self._metrics = AdaptiveQueueMetrics()
        self._wait_times: deque = deque(maxlen=1000)
        self._last_adjustment = asyncio.get_event_loop().time()
    
    async def put(self, item, timeout: float = 1.0):
        """Put item with backpressure."""
        if len(self._queue) >= self._size:
            # Apply backpressure
            await asyncio.sleep(timeout)
            if len(self._queue) >= self._size:
                raise asyncio.QueueFull()
        
        enqueue_time = asyncio.get_event_loop().time()
        self._queue.append((item, enqueue_time))
        self._metrics.total_enqueued += 1
        self._metrics.current_depth = len(self._queue)
    
    async def get(self):
        """Get item and record wait time."""
        while not self._queue:
            await asyncio.sleep(0.001)
        
        item, enqueue_time = self._queue.popleft()
        dequeue_time = asyncio.get_event_loop().time()
        wait_time = dequeue_time - enqueue_time
        
        self._wait_times.append(wait_time)
        self._metrics.total_dequeued += 1
        self._metrics.current_depth = len(self._queue)
        
        # Maybe adjust size
        await self._maybe_adjust_size()
        
        return item
    
    async def _maybe_adjust_size(self):
        """Adjust queue size based on metrics."""
        now = asyncio.get_event_loop().time()
        if now - self._last_adjustment < self._adjustment_interval:
            return
        
        if not self._wait_times:
            return
        
        # Calculate average wait time
        avg_wait = sum(self._wait_times) / len(self._wait_times)
        self._metrics.avg_wait_time = avg_wait * 1000  # Convert to ms
        
        # Adjust size
        target_wait_s = self._target_wait_ms / 1000
        
        if avg_wait > target_wait_s * 1.5:
            # Wait time too high, increase size
            new_size = min(int(self._size * 1.5), self._max_size)
            logger.info("Increasing queue size", old=self._size, new=new_size)
            self._size = new_size
            
        elif avg_wait < target_wait_s * 0.5 and self._size > self._min_size:
            # Wait time low, decrease size
            new_size = max(int(self._size * 0.8), self._min_size)
            logger.info("Decreasing queue size", old=self._size, new=new_size)
            self._size = new_size
        
        self._last_adjustment = now
```

**Design questions:**
1. What should the adjustment interval be?
2. How aggressive should size changes be (1.5x vs 2x)?
3. Should you use exponential moving average for wait time?

**Challenge:** Implement EWMA for smoother adjustments:
```python
# Exponential weighted moving average
alpha = 0.1  # Smoothing factor
ewma_wait_time = alpha * new_wait + (1 - alpha) * ewma_wait_time
```

### Circuit Breaker

Prevent cascading failures by stopping requests to unhealthy workers:

```python
from enum import Enum
import time

class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered

class CircuitBreaker:
    """
    Circuit breaker for worker health management.
    
    States:
    - CLOSED: Worker is healthy, all requests go through
    - OPEN: Worker is failing, reject requests
    - HALF_OPEN: Testing recovery, allow limited requests
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0,  # seconds in OPEN before testing
    ):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.last_failure_time = 0
    
    def call(self, func, *args, **kwargs):
        """Execute function through circuit breaker."""
        if self.state == CircuitState.OPEN:
            # Check if timeout elapsed
            if time.time() - self.last_failure_time > self.timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entering HALF_OPEN")
            else:
                raise CircuitBreakerOpen("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("Circuit breaker CLOSED (recovered)")
        else:
            # Reset failure count on success
            self.failure_count = 0
    
    def _on_failure(self):
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker OPEN (too many failures)")
```

**Design questions:**
1. What constitutes a "failure"? (timeout, exception, both?)
2. How many test requests in HALF_OPEN state?
3. Should timeout increase after multiple OPEN cycles?

### Health Monitoring

Workers report health; main process decides actions:

```python
@dataclass
class WorkerHealth:
    worker_id: str
    queue_depth: int
    avg_latency_ms: float
    error_rate: float  # Errors per second
    cpu_percent: float
    memory_mb: float
    last_update: float

class HealthMonitor:
    """
    Monitor worker health and take corrective action.
    """
    
    def __init__(self):
        self.worker_health: dict[str, WorkerHealth] = {}
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
    
    def update_health(self, health: WorkerHealth):
        """Update health metrics for a worker."""
        self.worker_health[health.worker_id] = health
        
        # Check if worker is unhealthy
        if self._is_unhealthy(health):
            self._handle_unhealthy_worker(health.worker_id)
    
    def _is_unhealthy(self, health: WorkerHealth) -> bool:
        """Determine if worker is unhealthy."""
        return (
            health.queue_depth > 1000 or
            health.avg_latency_ms > 1000 or
            health.error_rate > 10 or
            health.cpu_percent > 90
        )
    
    def _handle_unhealthy_worker(self, worker_id: str):
        """Take action on unhealthy worker."""
        logger.warning("Unhealthy worker detected", worker_id=worker_id)
        
        # Open circuit breaker
        if worker_id not in self.circuit_breakers:
            self.circuit_breakers[worker_id] = CircuitBreaker()
        
        # Could also:
        # - Reduce message rate to this worker
        # - Redirect messages to other workers
        # - Restart the worker process
```

**Implementation challenge:** Workers need to report health without blocking their main loop.

**Solution:** Separate health reporting task:
```python
async def health_reporting_loop(
    worker_id: str,
    metrics: WorkerMetrics,
    health_queue: mp.Queue
):
    """Periodically report health to main process."""
    while True:
        await asyncio.sleep(5.0)  # Report every 5 seconds
        
        health = WorkerHealth(
            worker_id=worker_id,
            queue_depth=metrics.queue_depth,
            avg_latency_ms=metrics.avg_latency_ms,
            error_rate=metrics.error_rate,
            cpu_percent=psutil.cpu_percent(),
            memory_mb=psutil.Process().memory_info().rss / 1024 / 1024,
            last_update=time.time(),
        )
        
        try:
            health_queue.put_nowait(health)
        except queue.Full:
            pass  # Drop if queue full
```

### Load Shedding

When overloaded, drop messages intelligently:

```python
class LoadShedder:
    """
    Intelligent message dropping under overload.
    """
    
    def __init__(self, max_queue_depth: int = 10000):
        self.max_queue_depth = max_queue_depth
        self.total_messages = 0
        self.dropped_messages = 0
    
    def should_accept(self, message: Message, current_queue_depth: int) -> bool:
        """Decide if message should be accepted or dropped."""
        self.total_messages += 1
        
        # Always drop if critically overloaded
        if current_queue_depth > self.max_queue_depth:
            self.dropped_messages += 1
            logger.warning("Dropping message due to overload", 
                         message_id=message.message_id)
            return False
        
        # Priority-based dropping
        if current_queue_depth > self.max_queue_depth * 0.8:
            # Drop low priority messages
            if message.priority < 5:
                self.dropped_messages += 1
                return False
        
        return True
    
    def get_drop_rate(self) -> float:
        """Calculate current drop rate."""
        if self.total_messages == 0:
            return 0.0
        return self.dropped_messages / self.total_messages
```

**Question:** How do you decide which messages to drop? Considerations:
- Priority
- Age (drop old messages first?)
- Message type
- Source worker

### Little's Law Application

Use Little's Law to predict queue behavior:

```
L = λ × W

Where:
- L = Average number of items in queue
- λ = Arrival rate (messages/second)
- W = Average wait time (seconds)
```

**Application:**
```python
def predict_queue_depth(arrival_rate: float, avg_latency: float) -> float:
    """Predict queue depth using Little's Law."""
    return arrival_rate * avg_latency

def optimal_queue_size(arrival_rate: float, 
                      processing_time: float,
                      target_wait_time: float) -> int:
    """Calculate optimal queue size."""
    # Want: wait_time < target_wait_time
    # If arrival_rate × processing_time > num_workers, queue grows unbounded
    # Optimal size: buffer for bursts without excessive latency
    
    predicted_depth = arrival_rate * target_wait_time
    return int(predicted_depth * 1.5)  # 50% buffer
```

**Exercise:** Use your metrics to validate Little's Law. Does L = λ × W hold?

## Experiments to Run

### Experiment 1: Adaptive Queue Behavior

**Hypothesis:** Adaptive queues maintain target wait time across varying loads.

**Test:**
1. Start with low load (10% of capacity)
2. Gradually increase to 150% of capacity
3. Measure queue size and wait time over time

**Expected:**
- Queue grows as load increases
- Wait time stays near target
- System doesn't crash at overload

**Questions:**
- How quickly does queue adapt?
- Does it oscillate or converge smoothly?
- What's the steady-state size at different loads?

### Experiment 2: Circuit Breaker Effectiveness

**Test:**
1. Introduce failures in one worker (e.g., 50% of messages fail)
2. Measure impact on overall system
3. Verify circuit breaker opens
4. Verify recovery when failures stop

**Questions:**
- How long until circuit breaker opens?
- Does it prevent cascading failures?
- How long until recovery?

### Experiment 3: Load Shedding Fairness

**Test:**
1. Send messages at 200% of capacity
2. Use different priorities (1-10)
3. Measure drop rate by priority

**Expected:**
- Higher priority messages dropped less
- Drop rate proportional to overload
- System remains stable

### Experiment 4: Little's Law Validation

**Test:**
1. Measure λ (arrival rate), W (wait time), L (queue depth)
2. Calculate L_predicted = λ × W
3. Compare L_predicted to L_observed

**Questions:**
- Does Little's Law hold in your system?
- If not, why not? (Hint: variable processing time, GC pauses)

### Experiment 5: Graceful Degradation

**Test:**
1. Gradually increase load from 50% → 200% of capacity
2. Measure:
   - Throughput
   - Latency (p50, p95, p99)
   - Drop rate
   - CPU/memory usage

**Expected:**
- Throughput plateaus at capacity
- Latency increases smoothly (not cliff)
- Drop rate increases smoothly
- System doesn't crash

## Success Criteria

✅ **Adaptive behavior working:**
- Queues adjust size automatically
- Target wait times maintained
- No manual tuning needed

✅ **Circuit breakers preventing failures:**
- Unhealthy workers detected
- Requests stopped to failing workers
- Automatic recovery on health restoration

✅ **Graceful degradation observed:**
- System stable under overload
- Performance degrades smoothly
- No catastrophic failures

✅ **Observable system:**
- All control decisions logged
- Metrics exposed via API
- Can explain why system made decisions

## Key Insights to Discover

1. **Fixed capacity is fragile** - Adaptive is robust
   - Fixed queues either waste memory or drop messages
   - Adaptive queues adjust to workload
   
2. **Backpressure must be system-wide** - Local solutions fail
   - Backpressure at one point cascades
   - Need coordinated response across all components
   
3. **Failure isolation is critical** - One bad worker affects others
   - Circuit breakers prevent cascading failures
   - Health checks enable proactive response
   
4. **Graceful degradation > perfect performance** - Availability matters
   - Better to serve some requests slowly than none at all
   - Drop low-priority work to protect high-priority

## Questions to Guide Implementation

**Architecture:**
1. Where should adaptive logic live? (centralized vs distributed)
2. How do you coordinate backpressure across processes?
3. What metrics are most important to track?

**Implementation:**
1. How often should you check health? (tradeoff: responsiveness vs overhead)
2. Should circuit breakers be per-worker or per-worker-type?
3. How do you prevent oscillation in adaptive sizing?

**Tuning:**
1. What are reasonable default values?
2. How do you tune for your specific workload?
3. Should tuning be manual or automatic?

## Hints & Tips

**Hint 1: Preventing oscillation**
```python
# Use hysteresis: different thresholds for increase/decrease
if avg_wait > target * 1.5:
    increase_size()
elif avg_wait < target * 0.3:  # Note: 0.3, not 0.5
    decrease_size()
```

**Hint 2: Smooth adjustments**
```python
# Don't jump directly to target, move gradually
target_size = calculate_optimal_size()
current_size = self._size

# Move 20% toward target each adjustment
new_size = current_size + 0.2 * (target_size - current_size)
self._size = int(new_size)
```

**Hint 3: Testing circuit breakers**
```python
# Inject failures for testing
class FlakyWorker:
    def __init__(self, failure_rate=0.5):
        self.failure_rate = failure_rate
    
    def process(self, msg):
        if random.random() < self.failure_rate:
            raise Exception("Simulated failure")
        return normal_processing(msg)
```

## What's Next?

Phase 7 makes the system robust to overload but doesn't address production concerns like observability, logging, and recovery. Phase 8 adds:
- Comprehensive metrics
- Structured logging
- Event sourcing
- Graceful shutdown
- Health endpoints

This **motivates Phase 8**: Production hardening.
