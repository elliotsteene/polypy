# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a learning project focused on concurrent state management in Python, specifically building an order book system that processes high-frequency market data messages. The project progresses through 8 phases, each exploring different concurrency patterns (sync baseline, asyncio, multiprocessing, shared memory, actor model, serialization optimization, backpressure/flow control, and production hardening).

## Development Commands

- **Run the application**: `just run` or `uv run src/main.py`
- **Install dependencies**: `uv sync`
- **Lint code**: `uv run ruff check .`
- **Format code**: `uv run ruff format .`

## Architecture

### Core Components

The system implements a message routing and state management pattern with three main components:

1. **MessageSource** (`src/message_source.py`): Generates a stream of market data messages (initial order books and price updates) for simulated workers. Each worker gets a `BookMessage` on first contact, then receives `PriceChangeMessage` updates.

2. **MessageRouter** (`src/message_router.py`): Routes incoming messages to the appropriate StateMachine based on `worker_id`. Creates new StateMachines on-demand when a worker sends its first message.

3. **StateMachine** (`src/state_machine.py`): Maintains an in-memory order book for a single worker using sorted price levels. Bids are sorted descending (highest first), asks ascending (lowest first). Uses both a sorted list and a dictionary for O(log n) inserts and O(1) lookups/updates.

### Message Flow

1. `MessageSource.message_stream()` yields messages at a configurable rate
2. `MessageRouter.route_message()` dispatches to the appropriate StateMachine
3. StateMachine processes either:
   - `BookMessage`: Initial order book snapshot (aggregates multiple orders at same price)
   - `PriceChangeMessage`: Update to a specific price level (replaces total size)

### State Machine Order Book Management

The StateMachine maintains two order books (bids and asks) using a hybrid data structure:
- **List of PriceLevels**: Maintains sorted order (binary search for inserts)
- **Price-to-index map**: Provides O(1) lookup for updates/deletes

Key operations:
- **Book message processing** (`_process_book_message`): Aggregates sizes at each price level
- **Price change processing** (`_process_price_change`): Updates, inserts, or removes price levels
- **Update logic**: Size=0 removes the level, size>0 replaces or creates it
- **Index synchronization**: When inserting/removing, all affected indices in the map are updated

## Learning Path Context

This codebase is part of a structured 8-phase learning curriculum (see `lessons/`). The current implementation is Phase 1 (single-threaded baseline). Future phases will introduce asyncio, multiprocessing, shared memory, actor patterns, msgspec serialization, backpressure handling, and production observability.
