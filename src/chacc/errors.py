"""
Errors raised by ChaCC, and turning pip's output into readable error messages.
"""

import re
from typing import Dict, List, Optional


class ResolutionError(RuntimeError):
    """Raised when dependencies cannot be resolved. Nothing is written to the cache."""

    def __init__(self, message: str, conflicts: Optional[List[Dict]] = None, module: Optional[str] = None):
        super().__init__(message)
        self.conflicts = conflicts or []
        self.module = module


def not_a_conflict(message: str) -> ResolutionError:
    """
    A ResolutionError that resolving modules one by one can't help with: a timeout, a
    missing constraint file, pip-compile not runnable, or the package index unreachable.
    """
    error = ResolutionError(message)
    error._not_a_conflict = True
    return error


def is_not_a_conflict(error: BaseException) -> bool:
    """True for errors made by :func:`not_a_conflict`."""
    return getattr(error, '_not_a_conflict', False)


NETWORK_ERROR_MARKERS = (
    "NewConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ProxyError", "SSLError",
    "Max retries exceeded", "Temporary failure in name resolution", "Name or service not known",
    "Network is unreachable", "Connection refused", "Connection reset",
)

# pip messages that mean a file it was given couldn't be read.
FILE_ERROR_MARKERS = (
    "Could not open requirements file", "Could not open constraint file", "No such file or directory",
)

_PYTHON_EXCEPTION_LINE = re.compile(r'^[A-Za-z_]\w*(\.\w+)+: ')


def summarize_pip_error(output: str, max_lines: int = 25) -> str:
    """Drop Python traceback blocks from pip/pip-compile output, keeping the actual messages."""
    lines = []
    exception_lines = set()
    in_traceback = False
    for line in output.strip().splitlines():
        if line.startswith("Traceback (most recent call last):"):
            in_traceback = True
            continue
        if in_traceback:
            if line[:1].isspace():
                continue
            in_traceback = False
        if _PYTHON_EXCEPTION_LINE.match(line):
            # e.g. "pip._internal.exceptions.InstallationError: ...": usually repeats pip's
            # own ERROR line, but sometimes it is the only explanation, so kept for now.
            exception_lines.add(len(lines))
        if line.startswith(("During handling of the above exception", "The above exception was")):
            continue
        if line.strip():
            lines.append(line.rstrip())
    if any(line.startswith("ERROR:") for line in lines):
        lines = [line for i, line in enumerate(lines) if i not in exception_lines]
    return "\n".join(lines[-max_lines:]) or output.strip()[-2000:]
