from http.client import HTTPConnection
from pathlib import Path
import threading

from server import create_server


def _start_server(root: Path):
    server = create_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, method, path, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def test_server_serves_full_file_and_byte_ranges(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"0123456789")
    server, thread = _start_server(tmp_path)
    try:
        response, body = _request(server, "GET", "/clip.mp4")
        assert response.status == 200
        assert body == b"0123456789"
        assert response.getheader("Accept-Ranges") == "bytes"

        response, body = _request(
            server, "GET", "/clip.mp4", {"Range": "bytes=2-5"}
        )
        assert response.status == 206
        assert body == b"2345"
        assert response.getheader("Content-Range") == "bytes 2-5/10"
        assert response.getheader("Content-Length") == "4"

        response, body = _request(server, "GET", "/clip.mp4", {"Range": "bytes=-3"})
        assert response.status == 206
        assert body == b"789"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_rejects_unsatisfiable_range(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"video")
    server, thread = _start_server(tmp_path)
    try:
        response, body = _request(server, "GET", "/clip.mp4", {"Range": "bytes=99-"})
        assert response.status == 416
        assert body == b""
        assert response.getheader("Content-Range") == "bytes */5"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_subtitle_endpoint_converts_srt_to_vtt(tmp_path):
    subtitle_dir = tmp_path / "subtitles"
    subtitle_dir.mkdir()
    (subtitle_dir / "episode.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,250\nHello\n", encoding="utf-8"
    )
    server, thread = _start_server(tmp_path)
    try:
        response, body = _request(server, "GET", "/subtitles/episode.srt")
        assert response.status == 200
        assert response.getheader("Content-Type").startswith("text/vtt")
        assert body.decode("utf-8").startswith("WEBVTT\n\n")
        assert b"00:00:00.000 --> 00:00:01.250" in body

        response, body = _request(server, "GET", "/subtitles/missing.srt")
        assert response.status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
