from enum import IntEnum, auto
from typing import Awaitable, Callable

from src.messages.protocol import ParsedMessage


class ConnectionStatus(IntEnum):
    """Connection Lifecycle status"""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    DRAINING = auto()  # Marked for recycling, no new subscriptions
    CLOSED = auto()  # Permanently closed


MessageCallback = Callable[[str, ParsedMessage], Awaitable[None]]
