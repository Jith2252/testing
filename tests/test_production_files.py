"""Production must never serve private repository or database files."""
from http.client import HTTPConnection
import threading

from auth import hash_password
from server import create_server


def test_production_blocks_private_files_for_get_and_head(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTION", "1")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("test-password"))
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    for name in (".env", "server.py", "private.db"):
        (tmp_path / name).write_text("private-data")
    server = create_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for method in ("GET", "HEAD"):
            for path in ("/.env", "/server.py", "/streams.db", "/private.db", "/%2eenv", "/.git/config"):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    assert response.status == 404
                    assert b"private-data" not in response.read()
                finally:
                    connection.close()
    finally:
        server.shutdown()
        server.server_close()
        server.store.close()
        thread.join(timeout=2)
