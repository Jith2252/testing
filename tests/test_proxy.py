from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import quote

import pytest

import server as server_module
from server import ProxyRequestError, create_server, validate_proxy_target


class MockMediaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    payload = b"0123456789"

    def do_GET(self):
        body = self.payload
        status = 200
        content_range = None
        range_header = self.headers.get("Range")
        if range_header == "bytes=2-5":
            body = self.payload[2:6]
            status = 206
            content_range = "bytes 2-5/10"
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _start_mock_server():
    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockMediaHandler)
    thread = threading.Thread(target=mock.serve_forever, daemon=True)
    thread.start()
    return mock, thread


def _start_server(root: Path):
    app = create_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    return app, thread


def _request(app, path, headers=None):
    connection = HTTPConnection("127.0.0.1", app.server_port, timeout=5)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def test_proxy_streams_local_mock_and_passes_range(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PROXY", "1")
    mock, mock_thread = _start_mock_server()
    app, app_thread = _start_server(tmp_path)
    target = f"http://127.0.0.1:{mock.server_port}/stream.mp4"
    monkeypatch.setattr(server_module, "validate_proxy_target", lambda url: url)
    try:
        response, body = _request(app, f"/proxy?url={quote(target, safe='')}" )
        assert response.status == 200
        assert body == b"0123456789"
        assert response.getheader("Access-Control-Allow-Origin") == "*"
        assert response.getheader("Content-Type") == "video/mp4"

        response, body = _request(
            app,
            f"/proxy?url={quote(target, safe='')}",
            {"Range": "bytes=2-5"},
        )
        assert response.status == 206
        assert body == b"2345"
        assert response.getheader("Content-Range") == "bytes 2-5/10"
    finally:
        app.shutdown()
        app.server_close()
        app_thread.join(timeout=2)
        mock.shutdown()
        mock.server_close()
        mock_thread.join(timeout=2)


def test_proxy_rejects_private_ip_targets():
    with pytest.raises(ProxyRequestError) as error:
        validate_proxy_target("http://127.0.0.1:8000/stream.mp4")
    assert error.value.status == 403


def test_proxy_rejects_non_http_schemes(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PROXY", "1")
    app, thread = _start_server(tmp_path)
    try:
        response, _body = _request(app, f"/proxy?url={quote('file:///tmp/movie.mp4', safe='')}")
        assert response.status == 400
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=2)


def test_proxy_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ENABLE_PROXY", raising=False)
    app, thread = _start_server(tmp_path)
    try:
        response, _body = _request(app, "/proxy?url=http%3A%2F%2F127.0.0.1%3A8000%2Fstream.mp4")
        assert response.status == 403
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=2)
