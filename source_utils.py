"""Shared URL source classification and validation rules."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

SOURCE_TYPES = ("mp4", "webm", "m3u8", "mpd", "unknown")
_ALLOWED_SCHEMES = {"http", "https"}
_SUFFIXES = (
    (".m3u8", "m3u8"),
    (".mpd", "mpd"),
    (".mp4", "mp4"),
    (".webm", "webm"),
)


def _split_source(source: object):
    if not isinstance(source, str):
        return None
    value = source.strip()
    if not value:
        return None
    try:
        return urlsplit(value)
    except ValueError:
        return None


def detect_source_type(source: object) -> str:
    """Return a media type based on the URL path, ignoring its query string."""

    parsed = _split_source(source)
    if parsed is None:
        return "unknown"
    path = parsed.path.lower()
    for suffix, source_type in _SUFFIXES:
        if path.endswith(suffix):
            return source_type
    return "unknown"


def is_http_url(source: object) -> bool:
    """Return whether the value is an absolute HTTP(S) URL with a host."""

    parsed = _split_source(source)
    return bool(parsed and parsed.scheme.lower() in _ALLOWED_SCHEMES and parsed.hostname)


def is_mixed_content(source: object, page_protocol: str = "http:") -> bool:
    """Return whether an HTTP URL would be mixed content on an HTTPS page."""

    parsed = _split_source(source)
    protocol = (page_protocol or "").lower().rstrip(":")
    return bool(parsed and protocol == "https" and parsed.scheme.lower() == "http")


def validate_source_url(source: object, page_protocol: str = "http:") -> dict[str, object]:
    """Validate an HTTP(S) media URL and return data shared with the browser UI."""

    value = source.strip() if isinstance(source, str) else ""
    parsed = _split_source(value)
    if parsed is None:
        return {"valid": False, "url": value, "type": "unknown", "mixed_content": False, "error": "Enter a source URL."}
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return {"valid": False, "url": value, "type": "unknown", "mixed_content": False, "error": "Only http:// and https:// URLs are supported."}
    try:
        port = parsed.port
    except ValueError:
        port = None
        invalid_port = True
    else:
        invalid_port = False
    if not parsed.hostname or invalid_port:
        return {"valid": False, "url": value, "type": "unknown", "mixed_content": False, "error": "Enter a complete URL with a valid host."}
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    mixed = is_mixed_content(normalized, page_protocol)
    return {
        "valid": True,
        "url": normalized,
        "type": detect_source_type(normalized),
        "mixed_content": mixed,
        "error": "This HTTP URL is blocked as mixed content on an HTTPS page." if mixed else None,
    }
