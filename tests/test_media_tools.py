import json
import io
from http.client import HTTPConnection
import threading
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest

import media_tools
from server import create_server
from server import _is_blocked_ip


def test_probe_normalizes_tracks_and_flags_image_subtitles():
    payload = {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        {"index": 1, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "tel"}},
        {"index": 2, "codec_type": "subtitle", "codec_name": "subrip", "disposition": {"default": 1}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
    ]}
    with patch.object(media_tools, "run_job", return_value=json.dumps(payload).encode()):
        tracks = media_tools.inspect("https://example.com/video.mkv", None)
    assert [track["index"] for track in tracks] == [1, 2, 3]
    assert tracks[0]["language"] == "tel"
    assert tracks[1]["default"] and tracks[1]["supported"]
    assert not tracks[2]["supported"]


def test_extraction_selects_absolute_track_and_bounds_duration():
    with patch.object(media_tools, "run_job", return_value=b"WEBVTT\n") as job:
        body, kind = media_tools.extract("https://example.com/a.mkv", {"index": 4, "kind": "subtitle", "supported": True}, 90, None)
    assert body.startswith(b"WEBVTT") and kind.startswith("text/vtt")
    assert "0:4" in job.call_args.args[3]
    assert job.call_args.kwargs["start"] == 90
    assert str(media_tools.CHUNK_SECONDS) in job.call_args.args[3]
    with pytest.raises(media_tools.MediaError, match="OCR"):
        media_tools.extract("https://example.com/a.mkv", {"index": 5, "kind": "subtitle", "supported": False}, 0, None)


def test_missing_tools_has_actionable_error(monkeypatch):
    monkeypatch.setattr(media_tools.shutil, "which", lambda _name: None)
    with pytest.raises(media_tools.MediaError, match="Install FFmpeg"):
        media_tools.run_job("ffprobe", "https://example.com/a.mkv", None, [])


def test_processing_errors_do_not_assume_missing_byte_ranges():
    assert "processing policy" in media_tools.processing_error("Protocol 'http' not on whitelist")
    assert "option" in media_tools.processing_error("Unrecognized option 'rw_timeout'")
    assert "22 seconds" in media_tools.processing_error("", timed_out=True)
    assert "byte ranges" not in media_tools.processing_error("unclassified failure")
    assert "secret" not in media_tools.processing_error("https://example.com/secret")


@pytest.mark.parametrize("address,blocked", [
    ("64:ff9b::8.8.8.8", False),
    ("64:ff9b::127.0.0.1", True),
    ("64:ff9b::10.0.0.1", True),
    ("64:ff9b::169.254.169.254", True),
    ("64:ff9b::192.0.2.1", True),
    ("64:ff9b::100.64.0.1", True),
    ("64:ff9b:1::8.8.8.8", True),
])
def test_nat64_policy_checks_embedded_ipv4_destination(address, blocked):
    assert _is_blocked_ip(address) is blocked


def test_relay_preserves_range_headers_and_bytes():
    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Length": "3", "Content-Range": "bytes 2-4/10", "Accept-Ranges": "bytes"}

    def upstream(url, headers, timeout):
        assert url == "https://example.com/video.mkv"
        assert headers["Range"] == "bytes=2-4"
        return Response(b"234")

    with media_tools.relay_source("https://example.com/video.mkv", upstream) as local:
        target = urlsplit(local)
        connection = HTTPConnection(target.hostname, target.port, timeout=5)
        try:
            connection.request("GET", target.path, headers={"Range": "bytes=2-4"})
            response = connection.getresponse()
            assert response.status == 206
            assert response.getheader("Content-Range") == "bytes 2-4/10"
            assert response.read() == b"234"
            assert not local.failure
        finally:
            connection.close()


def test_empty_audio_is_not_reported_as_success():
    with patch.object(media_tools, "run_job", return_value=b"RIFF" + b"\x00" * 96):
        with pytest.raises(media_tools.MediaError, match="No audio samples"):
            media_tools.extract("https://example.com/a.mkv", {"index": 2, "kind": "audio"}, 30, None)


def test_media_endpoints_require_active_stream_and_recheck_access(tmp_path):
    server = create_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, data=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(data) if data else None, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    try:
        url = "https://example.com/video.mkv"
        assert request("POST", "/api/media/inspect", {"url": url})[0] == 403
        row = server.store.create_stream(title="Demo", url=url)
        tracks = [{"index": 2, "kind": "subtitle", "supported": True, "label": "English"}]
        with patch("server.validate_proxy_target", return_value=url), patch.object(media_tools, "inspect", return_value=tracks):
            status, body = request("POST", "/api/media/inspect", {"url": url})
        assert status == 200
        token = json.loads(body)["token"]
        with patch.object(media_tools, "extract", return_value=(b"WEBVTT\n", "text/vtt")) as extract:
            assert request("GET", f"/api/media/chunk?token={token}&track=2&start=30")[0] == 200
            assert extract.call_args.args[2] == 30
            assert request("GET", f"/api/media/chunk?token={token}&track=99")[0] == 400
            assert request("GET", f"/api/media/chunk?token={token}&track=2&start=-1")[0] == 400
        server.store.update_stream(row["id"], enabled=False)
        assert request("GET", f"/api/media/chunk?token={token}&track=2")[0] == 403
        assert request("GET", "/api/media/chunk?token=invalid&track=2")[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        server.store.close()
        thread.join(timeout=2)
