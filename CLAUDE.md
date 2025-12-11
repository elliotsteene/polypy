# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a real-time market data processing system that connects to Polymarket's WebSocket API to maintain live orderbooks. The project is also a learning platform for exploring concurrent state management patterns in Python, progressing through 8 phases from synchronous baseline to production-hardened systems.

## Development Commands

### Local Development
- **Run the application**: `just run` or `uv run src/main.py`
- **Install dependencies**: `uv sync`
- **Lint and format**: `just check` (runs ruff check, format, and pyrefly)
- **Run tests**: `just tests`
- **Run specific test**: `just test {TEST NAME}` e.g `just test tests/test_parser.py::test_parse_messages`
- **Test with coverage**: `just test-cov` (requires 90% coverage)
- **Run checks + tests**: `just check-test`

### Docker Development
- **Start full stack**: `just stack-up` (builds image and starts services)
- **View logs**: `just docker-logs`
- **Stop stack**: `just stack-down`
- **Clean everything**: `just docker-clean` (removes volumes and images)

Services available when running Docker stack:
- **PolyPy**: http://localhost:8080
- **Stats endpoint**: http://localhost:8080/stats
- **Health endpoint**: http://localhost:8080/health
- **Metrics endpoint**: http://localhost:8080/metrics (Prometheus format)
- **Prometheus**: http://localhost:9090 (scrapes metrics every 15s)

### Git Workflow
- **Sync stack changes**: `just sync-stack-changes` (uses git-town)
- **Ship current branch**: `just ship` (merge and sync all branches)

## Architecture

### System Overview

The application uses a **multiprocessing architecture** with AsyncIO coordination:

```
┌─────────────────────────────────────────────────────────────────┐
│ PolyPy (app.py) - Main Orchestrator                             │
│  ├─ HTTPServer: /health, /stats, /metrics endpoints             │
│  ├─ AssetRegistry: Central market/asset state tracking          │
│  ├─ LifecycleController: Market discovery and expiration        │
│  ├─ ConnectionRecycler: Reconnect stale connections             │
│  ├─ ConnectionPool: Manages WebSocket connections (async)       │
│  ├─ MessageRouter: Routes parsed messages to workers (async)    │
│  └─ WorkerManager: Manages CPU-bound worker processes           │
│     └─ Worker Processes (multiprocessing): Update orderbooks    │
└─────────────────────────────────────────────────────────────────┘
```

### Core Message Flow

The system follows a streaming data pipeline with async-to-multiprocessing bridge:

1. **WebsocketConnection** (`src/connection/websocket.py`): Manages a single WebSocket connection to Polymarket's CLOB API
   - Connection lifecycle with exponential backoff (1s → 30s max)
   - Subscription management (up to 500 asset IDs per connection)
   - Raw message reception and stats tracking
   - Automatic reconnection on failures

2. **MessageParser** (`src/messages/parser.py`): Zero-copy parsing using msgspec
   - Decodes raw bytes to typed structures (generator pattern)
   - Three event types: book snapshots, price changes, last trade prices
   - Single price_change can contain updates for multiple assets

3. **MessageRouter** (`src/router.py`): Async-to-multiprocessing bridge
   - Batches messages from async domain (up to 100 msgs or 10ms timeout)
   - Routes to workers via multiprocessing queues using consistent hashing (by asset_id)
   - Handles backpressure with configurable timeouts (async queue: 20k, worker queue: 5k)

4. **WorkerManager** (`src/worker.py`): CPU-bound processing in separate OS processes
   - Each worker owns subset of orderbooks (determined by consistent hash)
   - Processes messages from dedicated multiprocessing queue
   - Reports health and stats periodically (5s heartbeat, 30s stats)
   - Bypasses Python GIL for true parallelism

5. **OrderbookStore** (`src/orderbook/orderbook_store.py`): Per-worker orderbook registry
   - Maps asset_id → OrderbookState
   - Maps market → list of asset_ids
   - Tracks memory usage across all books

6. **OrderbookState** (`src/orderbook/orderbook.py`): Individual orderbook using SortedDict
   - Bids: negated prices for descending sort (highest first)
   - Asks: natural order for ascending sort (lowest first)
   - Cached best bid/ask with invalidation
   - O(log n) inserts, O(1) lookups/updates

### Message Types

Protocol definitions in `src/messages/protocol.py` using msgspec.Struct:

- **BookSnapshot**: Full orderbook with bid/ask price levels (initial state)
- **PriceChange**: Single price level update (size=0 removes the level)
- **LastTradePrice**: Trade execution information
- **ParsedMessage**: Tagged union container (event_type determines which field is populated)

### Integer Price Representation

All prices and sizes use scaled integers to avoid floating-point precision issues:
- Prices: multiply by 1000 (PRICE_SCALE)
- Sizes: multiply by 100 (SIZE_SCALE)
- Example: "0.50" → 500 (price), "10.25" → 1025 (size)

### Latency Tracking

The application tracks two latency metrics (see `src/main.py`):
- **Network latency**: Time from server timestamp to local receipt
- **Process latency**: Time to apply updates to orderbook state

### Lifecycle Management

**AssetRegistry** (`src/registry/asset_registry.py`): Central state tracking for all markets/assets
- Thread-safe with RLock for multiprocessing coordination
- Tracks status transitions: pending → subscribed → expired
- Maps condition_id (market) → asset_ids → connection assignments

**LifecycleController** (`src/lifecycle/controller.py`): Market discovery and expiration
- Discovery loop: Fetches new markets from Gamma API every 60s
- Expiration loop: Checks for expired markets every 30s
- Cleanup loop: Removes stale expired markets after delay
- Uses callbacks to trigger connection pool updates

**ConnectionRecycler** (`src/lifecycle/recycler.py`): Connection health management
- Monitors connection age and health metrics
- Triggers recycling of stale/silent connections
- Coordinates with ConnectionPool for graceful reconnection

**ConnectionPool** (`src/connection/pool.py`): WebSocket connection pool
- Maintains multiple connections (each handles up to 500 assets)
- Batches pending subscriptions to minimize connections
- Coordinates with AssetRegistry for assignment tracking

**HTTPServer** (`src/server/server.py`): Observability endpoints
- `/health`: Application health status with component details
- `/stats`: Detailed statistics from all subsystems (registry, pool, router, workers, lifecycle, recycler)
- `/metrics`: Prometheus-format metrics (counters, gauges, summaries)

## Learning Path Context

This codebase is part of a structured 8-phase learning curriculum (see `lessons/`):

1. **Phase 1** (current): Single-threaded asyncio baseline
2. **Phase 2**: AsyncIO patterns and event loops
3. **Phase 3**: Multiprocessing for CPU parallelism
4. **Phase 4**: Shared memory for zero-copy patterns
5. **Phase 5**: Actor model for cross-worker communication
6. **Phase 6**: msgspec serialization optimization
7. **Phase 7**: Backpressure and flow control
8. **Phase 8**: Production observability and recovery

The `src/exercises/` directory contains reference implementations for learning exercises.

## Key Design Patterns

- **Multiprocessing architecture**: Worker processes bypass Python GIL for CPU-bound orderbook updates
- **Consistent hashing**: Router assigns assets to workers deterministically for load balancing
- **Async-to-multiprocessing bridge**: MessageRouter batches async messages and routes via mp.Queue
- **Generator-based parsing**: MessageParser yields messages for streaming processing
- **Zero-copy serialization**: msgspec.Struct for fast, memory-efficient data structures
- **Cached properties**: Best bid/ask computed once and invalidated on updates
- **Slots**: Used throughout for reduced memory footprint (memory efficiency)
- **Event-driven lifecycle**: AsyncIO coordination with proper signal handling (SIGINT, SIGTERM)
- **Exponential backoff**: Reconnection strategy prevents thundering herd (1s → 30s max)
- **Callback pattern**: LifecycleController uses callbacks to trigger pool updates without tight coupling

## Pull Request Workflow

After completing a phase of the implementation plan, use the **stacked-pr** skill to manage PR stacks:
- If on main branch: Creates a new PR stack
- If on feature branch: Appends a new change to the existing stack
- Command: Invoke the `Skill` tool with `skill: "stacked-pr"`
