#!/usr/bin/env python
"""
Test script for live Gamma API integration.

Run with: uv run python scripts/test_gamma_api.py
"""

import asyncio

import aiohttp
import structlog

from src.core.logging import configure as configure_logging
from src.lifecycle.api import fetch_active_markets

configure_logging()
logger = structlog.get_logger()


async def main():
    """Fetch and display market information from live Gamma API."""
    logger.info("Starting Gamma API test...")

    async with aiohttp.ClientSession() as session:
        try:
            markets = await fetch_active_markets(session, page_size=50)

            logger.info(f"Successfully fetched {len(markets)} markets")

            # Display first 5 markets
            for i, market in enumerate(markets[:5]):
                print(f"\n--- Market {i + 1} ---")
                print(f"Condition ID: {market.condition_id}")
                print(f"Question: {market.question[:80]}...")
                print(f"Outcomes: {market.outcomes}")
                print(f"Tokens: {len(market.tokens)}")
                print(f"End Date: {market.end_date_iso}")
                print(f"End Timestamp: {market.end_timestamp}")
                print(f"Active: {market.active}, Closed: {market.closed}")

                if market.tokens:
                    print("Token IDs:")
                    for token in market.tokens[:2]:
                        print(
                            f"  - {token.get('token_id', 'N/A')[:20]}... ({token.get('outcome', 'N/A')})"
                        )

            if len(markets) > 5:
                print(f"\n... and {len(markets) - 5} more markets")

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    asyncio.run(main())
