# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a real-time market data processing system that connects to Polymarket's WebSocket API to maintain live orderbooks. The project is also a learning platform for exploring concurrent state management patterns in Python, progressing through 8 phases from synchronous baseline to production-hardened systems.

## Development Commands

- **Run the application**: `just run` or `uv run src/main.py`
- **Install dependencies**: `uv sync`
- **Lint code**: `uv run ruff check .`
- **Format code**: `uv run ruff format .`
- **Run tests**: `uv run pytest`
- **Run specific test**: `uv run pytest tests/test_parser.py::test_parse_messages`

## Architecture

### Core Message Flow

The system follows a streaming data pipeline pattern:

1. **WebsocketConnection** (`src/connection/websocket.py`): Manages a single WebSocket connection to Polymarket's CLOB API. Handles:
   - Connection lifecycle (connect, reconnect with exponential backoff, graceful shutdown)
   - Subscription management (up to 500 asset IDs per connection)
   - Raw message reception and stats tracking
   - Automatic reconnection on failures

2. **MessageParser** (`src/messages/parser.py`): Zero-copy parsing of WebSocket messages using msgspec:
   - Decodes raw bytes to typed structures
   - Handles three event types: book snapshots, price changes, last trade prices
   - Uses generator pattern to yield ParsedMessage objects
   - Single price_change message can contain multiple updates for different assets

3. **OrderbookStore** (`src/orderbook/orderbook_store.py`): Central registry managing multiple orderbook states:
   - Maps asset_id → OrderbookState
   - Maps market → list of asset_ids
   - Tracks memory usage across all books

4. **OrderbookState** (`src/orderbook/orderbook.py`): Individual orderbook state using SortedDict for efficient price level management:
   - Bids stored with negated prices for descending sort (highest price first)
   - Asks stored naturally for ascending sort (lowest price first)
   - Cached best bid/ask prices with invalidation
   - O(log n) inserts, O(1) lookups and updates

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

### Connection Management

WebSocket connection features (`src/connection/websocket.py`):
- Automatic reconnection with exponential backoff (1s → 30s max)
- Health monitoring (tracks message silence >60s)
- Stats tracking (messages, bytes, parse errors, reconnect count)
- Graceful shutdown with signal handlers (SIGINT, SIGTERM)

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

- **Generator-based parsing**: MessageParser yields messages to enable streaming processing
- **Zero-copy serialization**: msgspec.Struct for fast, memory-efficient data structures
- **Cached properties**: Best bid/ask computed once and invalidated on updates
- **Slots**: Used throughout for reduced memory footprint
- **Event-driven lifecycle**: Asyncio-based with proper signal handling
- **Exponential backoff**: Reconnection strategy prevents thundering herd
