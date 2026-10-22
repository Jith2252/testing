"""Local web server for the video player.

Only the Python standard library is used.  The server serves files below a
configured directory, supports single byte ranges for media playback, and
converts ``/subtitles/<name>.srt`` requests to WebVTT on the fly.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import secrets
import signal
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.request import HTTPRedirectHandler

from source_utils import validate_source_url
from subs_parser import srt_to_vtt, vtt_to_text
from subtitle_style import normalize_subtitle_style
from auth import IPRateLimiter, SessionManager, client_ip, require_production_config, verify_password
from storage import create_store
import media_tools


PROXY_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
PROXY_DEFAULT_TIMEOUT_SECONDS = 15
PROXY_CHUNK_SIZE = 64 * 1024
SESSION_COOKIE = "stream_admin_session"
SESSION_TTL = 8 * 60 * 60


class ProxyRequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def proxy_enabled() -> bool:
    return os.environ.get("ENABLE_PROXY", "").strip() == "1"


def proxy_limits() -> tuple[int, float]:
    try:
        max_bytes = max(1, int(os.environ.get("PROXY_MAX_BYTES", PROXY_DEFAULT_MAX_BYTES)))
    except ValueError:
        max_bytes = PROXY_DEFAULT_MAX_BYTES
    try:
        timeout = max(0.1, float(os.environ.get("PROXY_TIMEOUT_SECONDS", PROXY_DEFAULT_TIMEOUT_SECONDS)))
    except ValueError:
        timeout = PROXY_DEFAULT_TIMEOUT_SECONDS
    return max_bytes, timeout


def production_enabled() -> bool:
    return os.environ.get("PRODUCTION", "") == "1"


def allow_embed() -> bool:
    return os.environ.get("ALLOW_EMBED", "") == "1"


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_json(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length > 5 * 1024 * 1024:
            raise ValueError("request body is too large")
        return json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON request") from error


def _is_blocked_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    # The well-known NAT64 prefix embeds an IPv4 destination. Apply the same
    # destination policy instead of treating its IPv6 reserved flag as private.
    if isinstance(address, ipaddress.IPv6Address) and address in ipaddress.ip_network("64:ff9b::/96"):
        address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return bool(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_proxy_target(url: str) -> str:
    """Validate a remote URL and reject private or loopback DNS targets."""

    validation = validate_source_url(url, "http:")
    if not validation["valid"]:
        raise ProxyRequestError(HTTPStatus.BAD_REQUEST, str(validation["error"]))
    parsed = urllib.parse.urlsplit(str(validation["url"]))
    if parsed.username or parsed.password:
        raise ProxyRequestError(HTTPStatus.BAD_REQUEST, "Proxy URLs must not contain credentials.")
    hostname = parsed.hostname
    if not hostname:
        raise ProxyRequestError(HTTPStatus.BAD_REQUEST, "Proxy URL has no host.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ProxyRequestError(HTTPStatus.BAD_REQUEST, "Proxy URL has an invalid port.") from error

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and _is_blocked_ip(str(literal)):
        raise ProxyRequestError(HTTPStatus.FORBIDDEN, "Private and loopback proxy targets are blocked.")

    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ProxyRequestError(HTTPStatus.BAD_GATEWAY, "Could not resolve the proxy target.") from error
    if not addresses:
        raise ProxyRequestError(HTTPStatus.BAD_GATEWAY, "Could not resolve the proxy target.")
    for address in addresses:
        candidate = address[4][0]
        try:
            if _is_blocked_ip(candidate):
                raise ProxyRequestError(HTTPStatus.FORBIDDEN, "Private and loopback proxy targets are blocked.")
        except ValueError as error:
            raise ProxyRequestError(HTTPStatus.BAD_GATEWAY, "Proxy target resolved to an invalid address.") from error
    return str(validation["url"])


def _open_proxy_target(url: str, headers: dict[str, str], timeout: float):
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(4):
        current = validate_proxy_target(current)
        request = urllib.request.Request(current, headers=headers, method="GET")
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308} and error.headers.get("Location"):
                current = urllib.parse.urljoin(current, error.headers["Location"])
                error.close()
                continue
            raise ProxyRequestError(HTTPStatus.BAD_GATEWAY, f"Remote resource returned HTTP {error.code}.") from error
        if response.status in {301, 302, 303, 307, 308} and response.headers.get("Location"):
            current = urllib.parse.urljoin(current, response.headers["Location"])
            response.close()
            continue
        return response
    raise ProxyRequestError(HTTPStatus.BAD_GATEWAY, "Too many redirects from proxy target.")


class VideoRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler with byte ranges and subtitle conversion."""

    server_version = "LocalVideoPlayer/1.0"

    def __init__(self, *args, directory: str | Path | None = None, **kwargs):
        super().__init__(*args, directory=str(directory) if directory is not None else None, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if not (allow_embed() and urllib.parse.urlsplit(self.path).path.startswith("/p/")):
            self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob: http: https:; connect-src 'self' http: https:; frame-ancestors 'self'"
            if not allow_embed() else
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob: http: https:; connect-src 'self' http: https:; frame-ancestors *",
        )
        super().end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/media/chunk":
            self._media_chunk()
            return
        if path == "/health":
            self._serve_health()
            return
        if path == "/config":
            self._serve_config()
            return
        if path == "/admin/login":
            self._serve_login()
            return
        if path == "/admin":
            if not self._require_admin(redirect=True):
                return
            self._serve_admin()
            return
        if path.startswith("/admin/api/"):
            if not self._require_admin():
                return
            self._serve_admin_api(path)
            return
        if path.startswith("/p/"):
            self._serve_player_page(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/api/stream/"):
            self._serve_stream(path.rsplit("/", 1)[-1])
            return
        if path == "/api/streams":
            self._serve_public_streams()
            return
        if path == "/proxy":
            self._serve_proxy()
            return
        asset = path.lstrip("/")
        if asset == "remote_tracks.js":
            self._send_file(asset, base=self.server.app_root)
            return
        if asset in {"admin.css", "admin.js", "admin_login.js", "styles.css", "player.js", "playlist.js", "source_utils.js", "subtitle_style.js", "vendor/hls.min.js", "vendor/dash.all.min.js"}:
            self._send_file(asset, base=self.server.app_root)
            return
        if self._is_subtitle_request():
            self._serve_subtitle()
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/media/inspect":
            self._media_inspect()
            return
        if path == "/admin/login":
            self._login()
            return
        if path == "/admin/logout":
            if self._require_admin() and self._require_csrf():
                self._logout()
            return
        if path.startswith("/admin/api/"):
            if self._require_admin() and self._require_csrf():
                self._admin_post(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/admin/api/") and self._require_admin() and self._require_csrf():
            self._admin_put(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/admin/api/") and self._require_admin() and self._require_csrf():
            self._admin_delete(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _store(self):
        return self.server.store

    def _media_allowed(self, url):
        if self._session():
            return True
        # Public playback only grants processing of enabled, unexpired sources.
        return any(row["url"] == url and self._store().get_by_slug(row["slug"], public=True)
                   for row in self._store().list_streams())

    def _media_limit(self):
        if not self.server.media_limiter.allow(client_ip(self.headers, self.client_address[0])):
            self._send_json({"error": "Too many media requests. Try again in a minute."}, HTTPStatus.TOO_MANY_REQUESTS)
            return False
        return True

    def _media_inspect(self):
        if not self._media_limit():
            return
        if "application/json" not in self.headers.get("Content-Type", ""):
            self._send_json({"error": "Use an application/json request."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            data = _read_json(self)
            url = data.get("url", "")
            if not isinstance(url, str) or not self._media_allowed(url):
                self._send_json({"error": "Sign in as admin or use an active shared stream to inspect media."}, HTTPStatus.FORBIDDEN)
                return
            validate_proxy_target(url)
            tracks = media_tools.inspect(url, _open_proxy_target)
            token = secrets.token_urlsafe(32)
            with self.server.media_lock:
                now = time.monotonic()
                self.server.media_sources = {key: item for key, item in self.server.media_sources.items() if item["expires"] > now}
                if len(self.server.media_sources) >= 128:
                    self._send_json({"error": "Media inspection is busy. Try again later."}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                self.server.media_sources[token] = {"url": url, "tracks": tracks, "expires": now + 6 * 3600}
            self._send_json({"token": token, "tracks": tracks, "chunkSeconds": media_tools.CHUNK_SECONDS}, headers={"Cache-Control": "no-store"})
        except (ValueError, AttributeError, ProxyRequestError, media_tools.MediaError) as error:
            message = str(error) if isinstance(error, media_tools.MediaError) else "Invalid or inaccessible media source."
            self._send_json({"error": message}, HTTPStatus.BAD_REQUEST)

    def _media_chunk(self):
        if not self._media_limit():
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            token = query.get("token", [""])[0]
            index = int(query.get("track", [""])[0])
            start = int(query.get("start", ["0"])[0])
            if start < 0 or start > 86400:
                raise ValueError("Invalid media position")
            with self.server.media_lock:
                source = self.server.media_sources.get(token)
            if not source or source["expires"] < time.monotonic() or not self._media_allowed(source["url"]):
                self._send_json({"error": "Media session expired or stream unavailable. Reload the video."}, HTTPStatus.FORBIDDEN)
                return
            track = next((item for item in source["tracks"] if item["index"] == index), None)
            if track is None:
                raise ValueError("Unknown media track")
            body, content_type = media_tools.extract(source["url"], track, start, _open_proxy_target)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, media_tools.MediaError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _session(self):
        return self.server.session_manager.verify(self._cookie(SESSION_COOKIE))

    def _require_admin(self, *, redirect=False):
        if self._session():
            return True
        if redirect:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/admin/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json({"error": "authentication required"}, HTTPStatus.UNAUTHORIZED)
        return False

    def _require_csrf(self):
        session = self._session()
        token = self.headers.get("X-CSRF-Token") or self.headers.get("X-CSRFToken")
        if not session or not self.server.session_manager.verify_csrf(session, token):
            self._send_json({"error": "CSRF token required"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def _send_json(self, value, status=HTTPStatus.OK, *, headers=None):
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, str(val))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name, *, content_type=None, replace=None, base=None):
        path = Path(base or self.directory or ".") / name
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        if replace:
            body = replace(body.decode("utf-8"))
            body = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_login(self):
        self._send_file("admin_login.html", content_type="text/html; charset=utf-8", base=self.server.app_root)

    def _serve_admin(self):
        session = self._session()
        csrf = self.server.session_manager.csrf_token(session)
        self._send_file("admin.html", content_type="text/html; charset=utf-8", base=self.server.app_root, replace=lambda text: text.replace('content=""', f'content="{csrf}"'))

    def _serve_admin_api(self, path):
        store = self._store()
        if path == "/admin/api/streams":
            self._send_json(store.list_streams())
        elif path == "/admin/api/audit":
            self._send_json(store.audit_log())
        elif path == "/admin/api/export":
            payload = store.export_streams()
            body = payload.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="streams-export.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _admin_post(self, path):
        store = self._store(); session = self._session(); admin = session.get("sub") if session else None
        try:
            data = _read_json(self)
            if path == "/admin/api/streams":
                row = store.create_stream(data); store.log_audit("create", admin=admin, stream_id=row["id"], detail={"slug": row["slug"]}); self._send_json(row, HTTPStatus.CREATED); return
            if path == "/admin/api/import":
                count = len(store.import_streams(data)); self._send_json({"imported": count}); return
            if path == "/admin/api/test":
                self._test_source(data.get("url", "")); return
            parts = path.split("/")
            if len(parts) == 6 and parts[-1] == "toggle":
                row = store.update_stream(parts[-2], enabled=bool(data.get("is_public", data.get("enabled", True))))
                if not row: self._send_json({"error": "stream not found"}, HTTPStatus.NOT_FOUND)
                else: self._send_json(row)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except (ValueError, KeyError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _admin_put(self, path):
        parts = path.split("/")
        if len(parts) != 5 or parts[3] != "streams":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found"); return
        try:
            row = self._store().update_stream(parts[4], _read_json(self))
            self._send_json(row or {"error": "stream not found"}, HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _admin_delete(self, path):
        parts = path.split("/")
        if len(parts) != 5 or parts[3] != "streams":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found"); return
        if self._store().delete_stream(parts[4]): self._send_json({"ok": True})
        else: self._send_json({"error": "stream not found"}, HTTPStatus.NOT_FOUND)

    def _test_source(self, url):
        try:
            target = validate_proxy_target(url)
            request = urllib.request.Request(target, method="HEAD", headers={"User-Agent": "StreamPlayerAdmin/1.0"})
            try:
                response = urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            content_type = response.headers.get("Content-Type", "")
            status = getattr(response, "status", getattr(response, "code", 0))
            self._send_json({"ok": 200 <= status < 400, "reachable": True, "status": status, "content_type": content_type, "cors_hint": "Source should send Access-Control-Allow-Origin for direct playback."})
            response.close()
        except (ProxyRequestError, OSError, urllib.error.URLError, TimeoutError) as error:
            status = error.status if isinstance(error, ProxyRequestError) else HTTPStatus.BAD_GATEWAY
            self._send_json({"ok": False, "reachable": False, "error": str(error), "cors_hint": "Source should send Access-Control-Allow-Origin for direct playback."}, status)

    def _login(self):
        ip = client_ip(self.headers, self.client_address[0])
        retry = self.server.login_limiter.retry_after(ip)
        if retry:
            self._send_json({"error": "too many login failures"}, HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": retry})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw or b"{}") if "json" in self.headers.get("Content-Type", "") else urllib.parse.parse_qs(raw.decode())
        except (ValueError, json.JSONDecodeError):
            data = {}
        username = data.get("username", [""])[0] if isinstance(data.get("username"), list) else data.get("username", "")
        password = data.get("password", [""])[0] if isinstance(data.get("password"), list) else data.get("password", "")
        if username != os.environ.get("ADMIN_USER", "admin") or not verify_password(password, os.environ.get("ADMIN_PASSWORD_HASH", "")):
            self.server.login_limiter.allow(ip)
            self._send_json({"error": "invalid credentials"}, HTTPStatus.UNAUTHORIZED); return
        self.server.login_limiter.reset(ip)
        token = self.server.session_manager.issue(username)
        cookie = f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if production_enabled() else "")
        self._send_json({"ok": True}, headers={"Set-Cookie": cookie})

    def _logout(self):
        token = self._cookie(SESSION_COOKIE); self.server.session_manager.logout(token)
        self._send_json({"ok": True}, headers={"Set-Cookie": f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})

    def _serve_player_page(self, slug):
        if not self._store().get_by_slug(slug, public=True):
            self.send_error(HTTPStatus.NOT_FOUND, "Stream not found")
            return
        self._send_file("index.html", content_type="text/html; charset=utf-8", base=self.server.app_root)

    def _serve_stream(self, slug):
        row = self._store().get_by_slug(slug, public=False)
        if not row or not row.get("enabled"):
            self.send_error(HTTPStatus.NOT_FOUND, "Stream not found"); return
        if row.get("expires_at") and not self._store().get_by_slug(slug, public=True):
            self.send_error(HTTPStatus.NOT_FOUND, "Stream expired"); return
        self._store().increment_view(row["id"], ip=client_ip(self.headers, self.client_address[0]))
        self._send_json({"title": row["title"], "url": row["url"], "type": row["type"]})

    def _serve_public_streams(self):
        rows = self._store().list_streams(public=True, available_only=True)
        self._send_json([{key: row[key] for key in ("slug", "title", "type")} for row in rows])

    def do_OPTIONS(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/proxy":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, If-Range")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_HEAD(self) -> None:
        if self._is_subtitle_request():
            self._serve_subtitle(head_only=True)
            return
        try:
            super().do_HEAD()
        finally:
            # SimpleHTTPRequestHandler does not copy the body for HEAD, so
            # close the file opened by our range-aware send_head explicitly.
            limited = getattr(self, "_range_file", None)
            if limited is not None:
                limited[0].close()
                self._range_file = None

    def _serve_proxy(self) -> None:
        if not proxy_enabled():
            self.send_error(HTTPStatus.FORBIDDEN, "Remote proxy is disabled. Set ENABLE_PROXY=1 to enable it.")
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        target = query.get("url", [""])[0]
        if not target:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing url query parameter.")
            return

        max_bytes, timeout = proxy_limits()
        try:
            target = validate_proxy_target(target)
            request_headers = {"Accept-Encoding": "identity", "User-Agent": "LocalVideoPlayerProxy/1.0"}
            for name in ("Range", "If-Range", "Accept"):
                value = self.headers.get(name)
                if value:
                    request_headers[name] = value
            response = _open_proxy_target(target, request_headers, timeout)
        except ProxyRequestError as error:
            self.send_error(error.status, error.message)
            return
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"Could not fetch remote resource: {error}")
            return

        try:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Remote resource exceeds the proxy size limit.")
                return
            self.send_response(response.status)
            content_type = response.headers.get("Content-Type") or "application/octet-stream"
            self.send_header("Content-Type", content_type)
            if content_length:
                self.send_header("Content-Length", content_length)
            for name in ("Content-Range", "Accept-Ranges", "ETag", "Last-Modified", "Cache-Control"):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges, ETag, Last-Modified")
            self.end_headers()

            deadline = time.monotonic() + timeout
            total = 0
            while total < max_bytes and time.monotonic() < deadline:
                chunk = response.read(min(PROXY_CHUNK_SIZE, max_bytes - total))
                if not chunk:
                    break
                self.wfile.write(chunk)
                total += len(chunk)
            if total >= max_bytes and content_length is None:
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (OSError, TimeoutError):
            self.close_connection = True
        finally:
            response.close()

    def send_head(self):  # noqa: ANN001 - signature follows stdlib handler
        """Serve a file, honoring one RFC 7233 byte range when supplied."""

        path = Path(self.translate_path(self.path))
        if production_enabled():
            # Never expose repository files, credentials, or the local database.
            public_files = {
                "remote_tracks.js",
                "index.html", "admin.css", "admin.js", "admin_login.js",
                "styles.css", "player.js", "playlist.js", "source_utils.js",
                "subtitle_style.js", "vendor/hls.min.js", "vendor/dash.all.min.js",
            }
            root = self.server.app_root
            if path == root:
                path = root / "index.html"
            if path.resolve() not in {(root / name).resolve() for name in public_files}:
                self.send_error(HTTPStatus.NOT_FOUND, "File not found")
                return None
        if path.is_dir():
            return super().send_head()
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        range_header = self.headers.get("Range")
        byte_range = self._parse_range(range_header, size) if range_header else None
        if range_header and byte_range is None:
            handle.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        content_type = self.guess_type(str(path))
        if byte_range is None:
            start, end = 0, size - 1
            status = HTTPStatus.OK
        else:
            start, end = byte_range
            status = HTTPStatus.PARTIAL_CONTENT
            handle.seek(start)

        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if status is HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self._range_file = (handle, length)
        return handle

    def copyfile(self, source, outputfile) -> None:  # noqa: ANN001 - stdlib signature
        limited = getattr(self, "_range_file", None)
        if limited is None:
            return super().copyfile(source, outputfile)
        handle, length = limited
        try:
            remaining = length
            while remaining:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        finally:
            handle.close()
            self._range_file = None

    @staticmethod
    def _parse_range(value: str, size: int) -> tuple[int, int] | None:
        """Parse a single ``bytes=start-end`` range, or return ``None``."""

        if size <= 0 or not value.lower().startswith("bytes="):
            return None
        spec = value[6:].strip()
        if "," in spec:
            return None  # Multi-range responses are intentionally unsupported.
        start_text, separator, end_text = spec.partition("-")
        if not separator:
            return None
        try:
            if not start_text:
                suffix_length = int(end_text)
                if suffix_length <= 0:
                    return None
                return max(0, size - suffix_length), size - 1
            start = int(start_text)
            if start < 0 or start >= size:
                return None
            end = int(end_text) if end_text else size - 1
            if end < start:
                return None
            return start, min(end, size - 1)
        except ValueError:
            return None

    def _is_subtitle_request(self) -> bool:
        path = urllib.parse.urlsplit(self.path).path
        return path == "/subtitles" or path.startswith("/subtitles/")

    def _subtitle_path(self) -> Path | None:
        url_path = urllib.parse.urlsplit(self.path).path
        relative = urllib.parse.unquote(url_path[len("/subtitles/") :])
        if not relative or relative.startswith(("/", "\\")):
            return None
        root = Path(self.directory or Path.cwd()).resolve()
        for subtitle_root in ((root / "subtitles").resolve(), root):
            candidate = (subtitle_root / relative).resolve()
            try:
                candidate.relative_to(subtitle_root)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def _serve_subtitle(self, head_only: bool = False) -> None:
        path = self._subtitle_path()
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Subtitle file not found")
            return
        try:
            source = path.read_bytes()
            if path.suffix.lower() == ".srt":
                body = srt_to_vtt(source).encode("utf-8")
            elif path.suffix.lower() == ".vtt":
                body = vtt_to_text(source).encode("utf-8")
            else:
                self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Use .srt or .vtt subtitles")
                return
        except (OSError, UnicodeDecodeError, ValueError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_health(self) -> None:
        body = json.dumps(
            {
                "status": "ok",
                "uptime_seconds": max(0.0, time.monotonic() - self.server.start_time),
            }
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_config(self) -> None:
        payload = {"subtitleStyle": normalize_subtitle_style()}
        if proxy_enabled():
            payload["proxyEnabled"] = True
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(directory: str | Path = ".", host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Create, but do not start, the local video HTTP server."""

    root = Path(directory).resolve()
    handler = lambda *args, **kwargs: VideoRequestHandler(*args, directory=root, **kwargs)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.start_time = time.monotonic()
    server.app_root = Path(__file__).resolve().parent
    if production_enabled():
        require_production_config()
    server.store = create_store(database_url=os.environ.get("DATABASE_URL"), db_path=(root / "streams.db" if not os.environ.get("DATABASE_URL") else None))
    secret = os.environ.get("SECRET_KEY") or "development-secret-key-change-me-32-chars"
    server.session_manager = SessionManager(secret, ttl_seconds=SESSION_TTL)
    server.login_limiter = IPRateLimiter(5, 900)
    server.media_limiter = IPRateLimiter(30, 60)
    server.media_lock = threading.Lock()
    server.media_sources = {}
    return server


def run_server(directory: str | Path = ".", host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the server until interrupted."""

    server = create_server(directory, host, port)
    stop_event = threading.Event()
    def _stop(_signum, _frame):
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    print(f"Serving {Path(directory).resolve()} at http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local video player")
    parser.add_argument("--directory", "-d", default=".", help="directory to serve")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    run_server(args.directory, args.host, args.port)


if __name__ == "__main__":
    main()
