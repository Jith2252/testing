import json

import pytest

from auth import IPRateLimiter, SessionManager, client_ip, hash_password, validate_production_config, verify_password
from storage import SQLiteStore, Storage, create_store


def test_sqlite_schema_crud_projection_availability_and_roundtrip(tmp_path):
    store = SQLiteStore(tmp_path / "streams.db")
    first = store.create_stream(title="One", url="https://media.example/one.mp4")
    hidden = store.create_stream(title="Hidden", url="https://media.example/secret.webm", enabled=False, view_count=7)
    assert set(first) == {"id", "slug", "title", "url", "type", "enabled", "expires_at", "created_at", "view_count"}
    assert len(first["slug"]) == 8 and first["slug"].isalnum() and first["type"] == "mp4"
    assert hidden["type"] == "unknown"
    public = store.get_stream(first["id"], public=True)
    assert "url" not in public and public["available"] is True
    assert store.get_by_slug(hidden["slug"]) is None
    assert store.get_by_slug(first["slug"])["slug"] == first["slug"]
    assert store.increment_view(first["id"]) == 1
    exported = store.export_streams()
    imported = SQLiteStore(":memory:").import_streams(exported)
    assert [(x["slug"], x["view_count"]) for x in imported] == [(x["slug"], x["view_count"]) for x in store.list_streams()]
    assert {"time", "admin", "action", "detail"}.issubset(store.audit_log()[0])
    assert store.delete_stream(first["id"])
    store.close()


def test_store_factory_and_auth_session_csrf_logout_and_rate_limit(tmp_path):
    store = create_store(tmp_path / "factory.db")
    assert isinstance(store, Storage)
    store.close()
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$") and len(encoded.split("$")) == 3
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    errors = validate_production_config({"PRODUCTION": "1"})
    assert any("ADMIN_USER" in e for e in errors) and any("SECRET_KEY" in e for e in errors)
    assert not validate_production_config({"PRODUCTION": "0"})
    manager = SessionManager("x" * 32, ttl_seconds=10, clock=lambda: 100)
    token = manager.issue("admin")
    csrf = manager.csrf_token(token)
    assert manager.verify(token)["sub"] == "admin" and manager.verify_csrf(token, csrf)
    assert manager.logout(token) and manager.verify(token) is None
    limiter = IPRateLimiter()
    for _ in range(5):
        assert limiter.allow("198.51.100.4", now=0)
    assert not limiter.allow("198.51.100.4", now=1)
    assert limiter.retry_info("198.51.100.4", now=1)["retry_after"] == 899
    assert client_ip({"X-Forwarded-For": "192.0.2.1, 198.51.100.4"}, "127.0.0.1") == "198.51.100.4"
