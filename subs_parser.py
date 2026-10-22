"""Small, dependency-free SRT and WebVTT parser.

The parser deliberately keeps cue text as text (including line breaks) and
represents cue positions as seconds from the beginning of the media.  The
``CueIndex`` helper makes repeated lookups after seeking inexpensive without
requiring any mutable parser state.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import re
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Cue:
    """One subtitle cue.

    ``identifier`` is the optional SRT number or WebVTT cue identifier.  It
    is retained for conversion back to WebVTT but is not used for lookup.
    """

    start: float
    end: float
    text: str
    identifier: str | None = None

    @property
    def start_time(self) -> float:
        """Alias useful to callers that prefer an explicit time name."""

        return self.start

    @property
    def end_time(self) -> float:
        """Alias useful to callers that prefer an explicit time name."""

        return self.end


_TIMESTAMP_RE = re.compile(
    r"^(?P<hours>\d+):(?P<minutes>[0-5]?\d):(?P<seconds>[0-5]?\d)(?P<fraction>[,.]\d{1,3})$"
)
_SHORT_TIMESTAMP_RE = re.compile(
    r"^(?P<minutes>\d+):(?P<seconds>[0-5]?\d)(?P<fraction>[,.]\d{1,3})$"
)


def _as_text(source: str | bytes) -> str:
    if isinstance(source, bytes):
        return source.decode("utf-8-sig")
    return source.lstrip("\ufeff")


def parse_timestamp(value: str) -> float:
    """Parse an SRT/VTT timestamp into seconds.

    Both ``HH:MM:SS,mmm`` (SRT) and ``HH:MM:SS.mmm`` (VTT) are accepted.  VTT
    also permits the shorter ``MM:SS.mmm`` form.
    """

    value = value.strip()
    match = _TIMESTAMP_RE.match(value) or _SHORT_TIMESTAMP_RE.match(value)
    if not match:
        raise ValueError(f"invalid subtitle timestamp: {value!r}")

    parts = match.groupdict()
    hours = int(parts.get("hours") or 0)
    minutes = int(parts["minutes"])
    seconds = int(parts["seconds"])
    fraction = parts["fraction"][1:]
    milliseconds = int(fraction.ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def format_timestamp(seconds: float) -> str:
    """Format seconds as the canonical WebVTT ``HH:MM:SS.mmm`` form."""

    if seconds < 0:
        raise ValueError("subtitle timestamps cannot be negative")
    total_milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _parse_timing_line(line: str) -> tuple[float, float]:
    if "-->" not in line:
        raise ValueError(f"invalid subtitle timing line: {line!r}")
    left, right = line.split("-->", 1)
    # WebVTT cue settings follow the end timestamp; they are not part of it.
    end_token = right.strip().split(None, 1)[0]
    start = parse_timestamp(left)
    end = parse_timestamp(end_token)
    if end < start:
        raise ValueError("subtitle cue ends before it starts")
    return start, end


def _blocks(source: str) -> Iterable[list[str]]:
    current: list[str] = []
    for line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            yield current
            current = []
    if current:
        yield current


def parse_srt(source: str | bytes) -> list[Cue]:
    """Parse SRT content and return cues in source order."""

    cues: list[Cue] = []
    for block in _blocks(_as_text(source)):
        timing_index = next(
            (index for index, line in enumerate(block) if "-->" in line), None
        )
        if timing_index is None:
            continue
        identifier = "\n".join(block[:timing_index]).strip() or None
        start, end = _parse_timing_line(block[timing_index])
        text = "\n".join(block[timing_index + 1 :]).strip()
        if text:
            cues.append(Cue(start, end, text, identifier))
    return cues


def parse_vtt(source: str | bytes) -> list[Cue]:
    """Parse WebVTT content and return cues in source order.

    Header metadata and NOTE/STYLE/REGION blocks are ignored.  A cue may have
    an identifier line before its timing line, as permitted by WebVTT.
    """

    text = _as_text(source).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        lines = lines[1:]

    cues: list[Cue] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        marker = lines[index].strip().upper()
        if marker in {"NOTE", "STYLE", "REGION"} or marker.startswith(("NOTE ", "STYLE ", "REGION ")):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue

        identifier: str | None = None
        timing_line = lines[index]
        if "-->" not in timing_line:
            identifier = timing_line.strip() or None
            index += 1
            if index >= len(lines) or "-->" not in lines[index]:
                # Malformed non-cue block: skip until its blank separator.
                while index < len(lines) and lines[index].strip():
                    index += 1
                continue
            timing_line = lines[index]

        start, end = _parse_timing_line(timing_line)
        index += 1
        cue_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cue_lines.append(lines[index])
            index += 1
        if cue_lines:
            cues.append(Cue(start, end, "\n".join(cue_lines).strip(), identifier))
    return cues


def parse_subtitles(source: str | bytes, format: str | None = None) -> list[Cue]:
    """Parse SRT or WebVTT, choosing from ``format`` or the content header."""

    if format:
        normalized = format.lower().lstrip(".")
        if normalized == "srt":
            return parse_srt(source)
        if normalized in {"vtt", "webvtt"}:
            return parse_vtt(source)
        raise ValueError(f"unsupported subtitle format: {format!r}")
    text = _as_text(source)
    if text.lstrip().upper().startswith("WEBVTT"):
        return parse_vtt(text)
    return parse_srt(text)


class CueIndex:
    """Immutable, seek-friendly cue lookup index."""

    def __init__(self, cues: Sequence[Cue]):
        self.cues = tuple(sorted(cues, key=lambda cue: (cue.start, cue.end)))
        self._starts = tuple(cue.start for cue in self.cues)

    def cue_at(self, position: float) -> Cue | None:
        """Return the cue active at ``position``, or ``None``.

        The end boundary is exclusive: at exactly ``cue.end`` the cue is no
        longer active.  This makes adjacent cues deterministic.
        """

        if not self.cues:
            return None
        candidate_index = bisect_right(self._starts, position) - 1
        if candidate_index < 0:
            return None
        candidate = self.cues[candidate_index]
        return candidate if candidate.start <= position < candidate.end else None

    lookup = cue_at
    find = cue_at


def cue_at(cues: Sequence[Cue], position: float) -> Cue | None:
    """Find the active cue after any seek position."""

    return CueIndex(cues).cue_at(position)


find_cue = cue_at
lookup_cue = cue_at


def srt_to_vtt(source: str | bytes) -> str:
    """Convert SRT content to valid WebVTT text."""

    cues = parse_srt(source)
    chunks = ["WEBVTT", ""]
    for cue in cues:
        if cue.identifier:
            chunks.extend([cue.identifier,])
        chunks.extend(
            [
                f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}",
                cue.text,
                "",
            ]
        )
    return "\n".join(chunks)


def vtt_to_text(source: str | bytes) -> str:
    """Return WebVTT text unchanged apart from a normalized BOM/newline."""

    return _as_text(source).replace("\r\n", "\n").replace("\r", "\n")
