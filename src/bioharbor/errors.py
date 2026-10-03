"""Errors that carry actionable guidance back to the agent."""

from __future__ import annotations


class BioHarborError(Exception):
    """Base error. `hint` tells the agent what to try next; `retryable` drives retries."""

    retryable = False

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint

    def to_dict(self) -> dict[str, object]:
        return {
            "error": type(self).__name__,
            "message": str(self),
            "hint": self.hint,
            "retryable": self.retryable,
        }


class InputValidationError(BioHarborError):
    """The input is malformed (bad FASTA, wrong alphabet, too long...)."""


class ToolUnavailableError(BioHarborError):
    """A required binary, model or database is missing. `bioharbor doctor` explains."""


class ToolExecutionError(BioHarborError):
    """An external program failed. The message carries the tail of its stderr."""


class ResourceUnavailableError(BioHarborError):
    """Not enough GPU/CPU resources right now. Safe to retry later."""

    retryable = True
