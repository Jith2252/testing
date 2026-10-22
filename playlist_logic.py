"""Pure playlist ordering helpers mirroring the browser playlist behavior.

The browser keeps files in the order returned by the multiple-file picker and
uses bounded previous/next navigation.  These helpers make that behavior
small and testable without needing a browser runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def playlist_order(files: Iterable[str]) -> list[str]:
    """Return files in picker order, materialized as a new list."""

    return list(files)


def next_index(index: int, total: int) -> int | None:
    """Return the next bounded index, or ``None`` when no next item exists."""

    if total <= 0 or index < 0 or index >= total - 1:
        return None
    return index + 1


def previous_index(index: int, total: int) -> int | None:
    """Return the previous bounded index, or ``None`` at the beginning."""

    if total <= 0 or index <= 0 or index >= total:
        return None
    return index - 1


def ordered_neighbor(files: Sequence[str], index: int, direction: int) -> str | None:
    """Return a neighboring filename in picker order.

    ``direction`` must be ``1`` for next or ``-1`` for previous.  Invalid
    directions and out-of-range indexes return ``None`` rather than wrapping.
    """

    if direction not in (-1, 1) or not files or not 0 <= index < len(files):
        return None
    target = index + direction
    if not 0 <= target < len(files):
        return None
    return files[target]
