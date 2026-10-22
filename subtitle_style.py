"""Validate subtitle appearance settings shared with the browser UI.

The public settings use JavaScript-style camelCase keys so they can be sent as
JSON without translation. Unknown keys are discarded, and normalization always
returns a new dictionary rather than changing caller-owned data.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import re


DEFAULT_SUBTITLE_STYLE = {
    "fontSize": 24,
    "color": "#ffffff",
    "backgroundOpacity": 0.5,
    "position": "bottom",
}

_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_POSITIONS = frozenset({"top", "middle", "bottom"})


def _number(value: object, default: float, minimum: float, maximum: float) -> float:
    """Coerce a finite number and keep it within the supported control range."""

    if isinstance(value, bool):
        return default
    try:
        if isinstance(value, str) and not value.strip():
            return default
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return min(max(number, minimum), maximum)


def normalize_subtitle_style(settings: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return safe subtitle style values, filling defaults and clamping numbers.

    Font size is an integer from 12 to 48 pixels; background opacity is from
    0 to 1. Colors must be six-digit hexadecimal CSS colors, and position is
    one of ``top``, ``middle``, or ``bottom``. Invalid values use the defaults.
    """

    values = settings if isinstance(settings, Mapping) else {}
    result = DEFAULT_SUBTITLE_STYLE.copy()
    size = _number(values.get("fontSize"), result["fontSize"], 12, 48)
    result["fontSize"] = math.floor(size + 0.5)
    result["backgroundOpacity"] = _number(
        values.get("backgroundOpacity"), result["backgroundOpacity"], 0, 1
    )
    color = values.get("color")
    if isinstance(color, str) and _COLOR.fullmatch(color):
        result["color"] = color.lower()
    position = values.get("position")
    if isinstance(position, str) and position in _POSITIONS:
        result["position"] = position
    return result
