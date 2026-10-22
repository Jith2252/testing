import json
from http.client import HTTPConnection
import threading
from unittest.mock import patch

import pytest

import media_tools
from server import create_server


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
