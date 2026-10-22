import json
from http.client import HTTPConnection
import threading

import pytest

import server as server_module
from auth import hash_password
from server import create_server


def _request(app, method, path, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", app.server_port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result_headers = dict(response.getheaders())
    connection.close()
    return response.status, result_headers, payload


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("correct"))
    monkeypatch.setenv("SECRET_KEY", "s" * 32)
    monkeypatch.delenv("PRODUCTION", raising=False)
    instance = create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown(); instance.server_close(); thread.join(timeout=2); instance.store.close()


def _login(app, password="correct"):
    status, headers, _ = _request(app, "POST", "/admin/login", json.dumps({"username": "admin", "password": password}), {"Content-Type": "application/json"})
    assert status == 200
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    _, _, html = _request(app, "GET", "/admin", headers={"Cookie": cookie})
    csrf = html.decode().split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]
    return cookie, csrf


def test_admin_redirect_login_and_csrf(app):
    assert _request(app, "GET", "/admin")[0] == 302
    cookie, csrf = _login(app)
    headers = {"Cookie": cookie, "Content-Type": "application/json"}
    assert _request(app, "POST", "/admin/api/streams", json.dumps({"title": "Nope", "url": "https://example.test/a.mp4"}), headers)[0] == 403
    headers["X-CSRF-Token"] = csrf
    status, _, body = _request(app, "POST", "/admin/api/streams", json.dumps({"title": "Demo", "url": "https://example.test/a.mp4"}), headers)
    assert status == 201
    assert json.loads(body)["slug"]


def test_public_stream_api_hides_urls_and_counts_views(app):
    cookie, csrf = _login(app)
    headers = {"Cookie": cookie, "Content-Type": "application/json", "X-CSRF-Token": csrf}
    _, _, body = _request(app, "POST", "/admin/api/streams", json.dumps({"title": "Demo", "url": "https://example.test/a.mp4"}), headers)
    row = json.loads(body)
    status, _, body = _request(app, "GET", f"/api/stream/{row['slug']}")
    assert status == 200 and json.loads(body)["url"].endswith("a.mp4")
    public = json.loads(_request(app, "GET", "/api/streams")[2])
    assert public == [{"slug": row["slug"], "title": "Demo", "type": "mp4"}]
    share_status, _, share_body = _request(app, "GET", f"/p/{row['slug']}")
    assert share_status == 200
    assert b'id="subtitleSelect"' in share_body
    assert app.store.get_by_slug(row["slug"], public=False)["view_count"] == 1
    app.store.update_stream(row["id"], enabled=False)
    assert _request(app, "GET", f"/api/stream/{row['slug']}")[0] == 404


def test_login_rate_limit_and_forwarded_ip(app):
    headers = {"Content-Type": "application/json", "X-Forwarded-For": "10.0.0.1, 198.51.100.9"}
    for _ in range(5):
        assert _request(app, "POST", "/admin/login", json.dumps({"username": "admin", "password": "wrong"}), headers)[0] == 401
    status, response_headers, _ = _request(app, "POST", "/admin/login", json.dumps({"username": "admin", "password": "wrong"}), headers)
    assert status == 429 and int(response_headers["Retry-After"]) > 0
    assert app.login_limiter.last_forwarded_ip == "198.51.100.9"


def test_production_rejects_weak_config(monkeypatch, tmp_path):
    monkeypatch.setenv("PRODUCTION", "1")
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        create_server(tmp_path, host="127.0.0.1", port=0)


def test_admin_test_rejects_private_and_reports_reachable(app, monkeypatch):
    cookie, csrf = _login(app)
    headers = {"Cookie": cookie, "Content-Type": "application/json", "X-CSRF-Token": csrf}
    assert _request(app, "POST", "/admin/api/test", json.dumps({"url": "http://127.0.0.1:1/a.mp4"}), headers)[0] == 403
    monkeypatch.setattr(server_module, "validate_proxy_target", lambda value: value)
    status, _, body = _request(app, "POST", "/admin/api/test", json.dumps({"url": "http://127.0.0.1:1/a.mp4"}), headers)
    assert status == 200 or status == 502
    assert "reachable" in json.loads(body)
