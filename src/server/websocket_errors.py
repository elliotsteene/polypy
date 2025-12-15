"""Custom error types for WebSocket operations."""

from __future__ import annotations


class WebSocketError(Exception):
    """Base exception for WebSocket-related errors."""

    pass


class WorkersNotAvailableError(WebSocketError):
    """Raised when workers are not available for orderbook queries."""

    pass


class InvalidMessageError(WebSocketError):
    """Raised when client sends an invalid message."""

    pass


class AssetNotFoundError(WebSocketError):
    """Raised when requested asset is not found in any worker."""

    pass
