"""Gamma API client for market discovery."""

import asyncio
import json
from datetime import datetime
from typing import Any

import aiohttp
import structlog

from src.core.logging import Logger
from src.lifecycle.types import GAMMA_API_BASE_URL, MarketInfo

logger: Logger = structlog.get_logger()

# API configuration
DEFAULT_PAGE_SIZE = 500
MAX_RETRIES = 3
RETRY_DELAY = 2.0
MARKETS_URL = f"{GAMMA_API_BASE_URL}/markets"


async def fetch_active_markets(
    session: aiohttp.ClientSession,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[MarketInfo]:
    """
    Fetch all active, non-closed markets from Gamma API.

    Handles pagination automatically, returning all markets.
    """
    all_markets: list[MarketInfo] = []
    offset = 0

    while True:
        params = {
            "limit": page_size,
            "offset": offset,
            "closed": "false",
            "order": "id",
            "ascending": "false",
        }

        markets, market_page_count = await _fetch_page(session, params)

        if not markets:
            logger.warning("No more markets")
            break

        all_markets.extend(markets)

        if market_page_count < page_size:
            logger.warning(
                f"markets less than page_size: {len(markets)} and {len(all_markets)} and {page_size}"
            )
            break

        offset += page_size

    logger.info(f"Fetched {len(all_markets)} active markets from Gamma API")
    return all_markets


async def _fetch_page(
    session: aiohttp.ClientSession,
    params: dict[str, Any],
) -> tuple[list[MarketInfo], int]:
    """Fetch a single page of markets with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(MARKETS_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return (
                    [_parse_market(m) for m in data if _is_valid_market(m)],
                    len(data),
                )

        except aiohttp.ClientError as e:
            logger.warning(
                f"Gamma API request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY * (2**attempt))
            else:
                logger.error(f"Gamma API request failed after {MAX_RETRIES} attempts")
                raise

    return ([], 0)  # Should not reach here


def _is_valid_market(data: dict[str, Any]) -> bool:
    """Check if market data has required fields."""
    required = ["conditionId", "question", "outcomes", "clobTokenIds", "endDate"]
    return all(field in data for field in required)


def _parse_market(data: dict[str, Any]) -> MarketInfo:
    """Parse raw API response into MarketInfo."""
    # Parse ISO date to unix milliseconds
    end_date_iso = data.get("endDate", "")
    end_timestamp = 0

    if end_date_iso:
        try:
            dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            end_timestamp = int(dt.timestamp() * 1000)
        except ValueError:
            logger.warning(f"Invalid endDate format: {end_date_iso}")

    # Parse outcomes from JSON string
    outcomes_raw = data.get("outcomes", '["Yes", "No"]')
    try:
        outcomes = (
            json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        )
    except json.JSONDecodeError:
        outcomes = ["Yes", "No"]

    # Parse token IDs from JSON string and create token list
    clob_token_ids_raw = data.get("clobTokenIds", "[]")
    try:
        clob_token_ids = (
            json.loads(clob_token_ids_raw)
            if isinstance(clob_token_ids_raw, str)
            else clob_token_ids_raw
        )
    except json.JSONDecodeError:
        logger.warning(f"Unable to parse clobTokenIds: {data['conditionId']}")
        clob_token_ids = []

    # Create tokens list with outcome mapping
    tokens = []
    for i, token_id in enumerate(clob_token_ids):
        outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
        tokens.append({"token_id": token_id, "outcome": outcome})

    return MarketInfo(
        condition_id=data["conditionId"],
        question=data.get("question", ""),
        outcomes=outcomes,
        tokens=tokens,
        end_date_iso=end_date_iso,
        end_timestamp=end_timestamp,
        active=data.get("active", True),
        closed=data.get("closed", False),
    )
