import pytest

from source_utils import detect_source_type, is_mixed_content, validate_source_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.test/movie.mp4?token=abc", "mp4"),
        ("https://example.test/movie.webm?download=1", "webm"),
        ("https://example.test/live.m3u8?token=abc", "m3u8"),
        ("https://example.test/manifest.mpd?token=abc", "mpd"),
        ("https://example.test/video.bin?format=mp4", "unknown"),
    ],
)
def test_detect_source_type_uses_path_and_ignores_query(url, expected):
    assert detect_source_type(url) == expected


def test_validate_source_url_rejects_non_http_schemes():
    result = validate_source_url("file:///tmp/movie.mp4")
    assert result["valid"] is False
    assert "http://" in result["error"]


def test_validate_source_url_flags_mixed_content():
    result = validate_source_url("http://media.example.test/movie.mp4", "https:")
    assert result == {
        "valid": True,
        "url": "http://media.example.test/movie.mp4",
        "type": "mp4",
        "mixed_content": True,
        "error": "This HTTP URL is blocked as mixed content on an HTTPS page.",
    }
    assert is_mixed_content("http://media.example.test/movie.mp4", "https:") is True
    assert is_mixed_content("https://media.example.test/movie.mp4", "https:") is False


def test_validate_source_url_requires_a_complete_url():
    result = validate_source_url("not a url")
    assert result["valid"] is False
    assert result["type"] == "unknown"
