import asyncio
import multiprocessing as mp

import structlog

from src.app import PolyPy
from src.core.logging import Logger
from src.core.logging import configure as configure_logging

configure_logging()

logger: Logger = structlog.get_logger()


async def main():
    logger.info("Starting PolyPy application...")
    max_workers = mp.cpu_count() - 1
    polypy = PolyPy(
        num_workers=max_workers,
        enable_http=True,
    )

    await polypy.run()

    await logger.ainfo("PolyPy finished")


if __name__ == "__main__":
    asyncio.run(main())
