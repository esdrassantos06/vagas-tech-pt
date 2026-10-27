"""Application error hierarchy with stable codes and HTTP status mapping."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error carrying a machine code, HTTP status, and optional details."""

    code = "INTERNAL_ERROR"
    status = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationError(AppError):
    """Input failed a business validation rule."""

    code = "VALIDATION_ERROR"
    status = 422


class UnauthorizedError(AppError):
    """Authentication failed or was not provided."""

    code = "UNAUTHORIZED"
    status = 401


class SourceError(AppError):
    """A source could not be fetched (network, timeout, or HTTP error status)."""

    code = "SOURCE_ERROR"
    status = 502


class ParseError(AppError):
    """A source responded but its markup no longer matches the parser."""

    code = "PARSE_ERROR"
    status = 502


class NotFoundError(AppError):
    """A requested resource does not exist."""

    code = "NOT_FOUND"
    status = 404

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} not found: {identifier}")


class ChannelError(AppError):
    """A notification channel (email, Discord, Telegram) failed to deliver."""

    code = "CHANNEL_ERROR"
    status = 502


class DestinationGoneError(ChannelError):
    """The destination no longer exists or the bot lost access for good."""

    code = "DESTINATION_GONE"


class RateLimitError(AppError):
    """Too many requests for a resource or target."""

    code = "RATE_LIMITED"
    status = 429
