"""Exception types shared across the tool."""

from __future__ import annotations


class QcMergeError(Exception):
    """An error that can be shown to the user as-is.

    The CLI catches only this exception and prints its message without a
    traceback.
    """
