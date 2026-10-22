from http.client import HTTPConnection
import json
from pathlib import Path
import threading

from server import create_server


def test_health_endpoint_returns_status_and_uptime(tmp_path: Path):
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = response.read()
        connection.close()

        assert response.status == 200
        assert response.getheader("Content-Type") == "application/json"

        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert set(payload) == {"status", "uptime_seconds"}
        assert isinstance(payload["uptime_seconds"], (int, float))
        assert 0 <= payload["uptime_seconds"] < 60
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
