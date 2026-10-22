from http.client import HTTPConnection
import json
import threading

from server import create_server
from subtitle_style import DEFAULT_SUBTITLE_STYLE


def test_config_returns_default_subtitle_style_as_json(tmp_path):
    server = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ("/config", "/config?cache=ignored"):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read()
                assert response.status == 200
                assert response.getheader("Content-Type") == "application/json"
                assert response.getheader("Content-Length") == str(len(body))
                assert json.loads(body) == {"subtitleStyle": DEFAULT_SUBTITLE_STYLE}
            finally:
                connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
