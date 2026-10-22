"""Storage backends for streams and the administration audit trail."""
from __future__ import annotations
import json, os, secrets, sqlite3, string, threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from source_utils import detect_source_type, validate_source_url
STREAM_FIELDS = ("id", "slug", "title", "url", "type", "enabled", "expires_at", "created_at", "view_count")
SLUG_ALPHABET = string.ascii_lowercase + string.digits

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def _iso_utc(value, *, nullable=False):
    if value is None or (nullable and str(value).strip()==""): return None if nullable else _now()
    if isinstance(value, datetime): parsed=value
    else:
        text=str(value).strip(); text=text[:-1]+"+00:00" if text.endswith("Z") else text
        try: parsed=datetime.fromisoformat(text)
        except ValueError as exc: raise ValueError("timestamps must be ISO 8601 values") from exc
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def _is_available(row, now=None):
    if not bool(row.get("enabled")): return False
    if not row.get("expires_at"): return True
    try:
        exp=datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")); exp=exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        return exp>(now or datetime.now(timezone.utc))
    except ValueError: return False
def _public(row):
    result={k:row[k] for k in STREAM_FIELDS if k in row and k!="url"}; result["available"]=_is_available(row); return result

class Store(ABC):
    @abstractmethod
    def list_streams(self, *, public=False, available_only=False): ...
    @abstractmethod
    def get_stream(self, stream_id, *, public=False): ...
    @abstractmethod
    def get_by_slug(self, slug, *, public=True): ...
    @abstractmethod
    def create_stream(self, stream=None, **fields): ...
    @abstractmethod
    def update_stream(self, stream_id, stream=None, **fields): ...
    @abstractmethod
    def delete_stream(self, stream_id): ...
    @abstractmethod
    def increment_view(self, stream_id, *, amount=1, admin=None, ip=None): ...
    @abstractmethod
    def audit_log(self, *, limit=100, stream_id=None): ...
    @abstractmethod
    def log_audit(self, action, *, admin=None, detail=None, stream_id=None): ...
    @abstractmethod
    def export_streams(self, *, public=False): ...
    @abstractmethod
    def import_streams(self, payload): ...

class _SQLStore(Store):
    backend="sqlite"; placeholder="?"
    def __init__(self): self._lock=threading.RLock()
    def _adapt(self, sql): return sql
    def _execute(self, sql, params=(), *, commit=False):
        with self._lock:
            cur=self.connection.cursor(); cur.execute(self._adapt(sql), tuple(params))
            if commit: self.connection.commit()
            return cur
    def _create_tables(self):
        stmts=["""CREATE TABLE IF NOT EXISTS streams (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, url TEXT NOT NULL, type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, expires_at TEXT NULL, created_at TEXT NOT NULL, view_count INTEGER NOT NULL DEFAULT 0)""", "CREATE INDEX IF NOT EXISTS streams_enabled_idx ON streams(enabled, expires_at)", """CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, time TEXT NOT NULL, admin TEXT NULL, action TEXT NOT NULL, detail TEXT NULL, stream_id TEXT NULL)""", "CREATE INDEX IF NOT EXISTS audit_time_idx ON audit_log(time)"]
        with self._lock:
            for stmt in stmts: self._execute(stmt)
            self.connection.commit()
    def close(self): self.connection.close()
    @staticmethod
    def _row(row):
        if row is None: return None
        result=dict(row)
        if "enabled" in result: result["enabled"]=bool(result["enabled"])
        if "view_count" in result: result["view_count"]=int(result["view_count"])
        return result
    def _fetch_one(self, sql, params=()): return self._row(self._execute(sql, params).fetchone())
    def _fetch_all(self, sql, params=()): return [x for x in (self._row(r) for r in self._execute(sql, params).fetchall()) if x is not None]
    @staticmethod
    def _data(stream, fields):
        d=dict(stream or {}); d.update(fields)
        if "source_url" in d and "url" not in d: d["url"]=d["source_url"]
        if "source_type" in d and "type" not in d: d["type"]=d["source_type"]
        if "is_public" in d and "enabled" not in d: d["enabled"]=d["is_public"]
        return d
    @staticmethod
    def _validated(data, existing=None):
        existing=existing or {}; title=str(data.get("title", existing.get("title", ""))).strip(); url=str(data.get("url", existing.get("url", ""))).strip()
        if not title: raise ValueError("Stream title is required")
        validation=validate_source_url(url)
        if not validation["valid"]: raise ValueError(str(validation["error"]))
        detected=detect_source_type(str(validation["url"])); detected="unknown" if detected=="webm" else detected
        typ=detected
        return {"title":title,"url":str(validation["url"]),"type":typ,"enabled":bool(data.get("enabled", existing.get("enabled",True))),"expires_at":_iso_utc(data.get("expires_at", existing.get("expires_at")), nullable=True),"view_count":max(0,int(data.get("view_count", existing.get("view_count",0))))}
    def _new_slug(self):
        for _ in range(20):
            slug="".join(secrets.choice(SLUG_ALPHABET) for _ in range(8))
            if self._fetch_one("SELECT slug FROM streams WHERE slug = "+self.placeholder,(slug,)) is None: return slug
        raise RuntimeError("could not allocate a unique stream slug")
    def create_stream(self, stream=None, **fields):
        d=self._data(stream,fields); v=self._validated(d); sid=str(d.get("id") or secrets.token_hex(16)); slug=str(d.get("slug") or self._new_slug())
        if len(slug)!=8 or not slug.isalnum(): raise ValueError("slug must be exactly 8 letters or digits")
        created_at = _iso_utc(d.get("created_at"), nullable=False)
        self._execute("INSERT INTO streams (id,slug,title,url,type,enabled,expires_at,created_at,view_count) VALUES (?,?,?,?,?,?,?,?,?)",(sid,slug,v["title"],v["url"],v["type"],int(v["enabled"]),v["expires_at"],created_at,v["view_count"]),commit=True); self.log_audit("create",stream_id=sid,detail={"slug":slug}); return self.get_stream(sid)
    add_stream=create_stream
    def get_stream(self, stream_id, *, public=False):
        row=self._fetch_one("SELECT id,slug,title,url,type,enabled,expires_at,created_at,view_count FROM streams WHERE id = "+self.placeholder,(stream_id,)); return _public(row) if row and public else row
    def get_by_slug(self, slug, *, public=True):
        row=self._fetch_one("SELECT id,slug,title,url,type,enabled,expires_at,created_at,view_count FROM streams WHERE slug = "+self.placeholder,(slug,))
        if not row or (public and not _is_available(row)): return None
        return _public(row) if public else row
    def list_streams(self, *, public=False, available_only=False, include_private=None):
        rows=self._fetch_all("SELECT id,slug,title,url,type,enabled,expires_at,created_at,view_count FROM streams ORDER BY created_at DESC")
        if public or available_only: rows=[r for r in rows if _is_available(r)]
        return [_public(r) if public else r for r in rows]
    def update_stream(self, stream_id, stream=None, **fields):
        cur=self.get_stream(stream_id)
        if cur is None: return None
        v=self._validated(self._data(stream,fields),cur); self._execute("UPDATE streams SET title=?,url=?,type=?,enabled=?,expires_at=?,view_count=? WHERE id=?",(v["title"],v["url"],v["type"],int(v["enabled"]),v["expires_at"],v["view_count"],stream_id),commit=True); self.log_audit("update",stream_id=stream_id); return self.get_stream(stream_id)
    def delete_stream(self, stream_id):
        ok=self._execute("DELETE FROM streams WHERE id = "+self.placeholder,(stream_id,),commit=True).rowcount>0
        if ok: self.log_audit("delete",stream_id=stream_id)
        return ok
    def increment_view(self, stream_id, *, amount=1, admin=None, ip=None, actor=None, ip_address=None):
        amount=int(amount)
        if amount<0: raise ValueError("amount must be non-negative")
        if self.get_stream(stream_id) is None: raise KeyError(stream_id)
        self._execute("UPDATE streams SET view_count=view_count+"+self.placeholder+" WHERE id="+self.placeholder,(amount,stream_id),commit=True); detail={"amount":amount}; addr=ip or ip_address
        if addr: detail["ip"]=addr
        self.log_audit("view",admin=admin or actor,stream_id=stream_id,detail=detail); return int(self.get_stream(stream_id)["view_count"])
    record_view=increment_view
    def view_count(self, stream_id): return int(self.get_stream(stream_id)["view_count"])
    get_view_count=view_count
    def log_audit(self, action, *, admin=None, detail=None, stream_id=None, actor=None, details=None, ip_address=None):
        if detail is None: detail=details
        encoded=detail if isinstance(detail,str) else json.dumps(detail if detail is not None else {},sort_keys=True,separators=(",", ":")); sql="INSERT INTO audit_log (time,admin,action,detail,stream_id) VALUES (?,?,?,?,?)" + (" RETURNING id" if self.backend == "postgres" else ""); cur=self._execute(sql,(_now(),admin or actor,action,encoded,stream_id),commit=True); row=cur.fetchone() if self.backend == "postgres" else None; return int((row["id"] if row else getattr(cur,"lastrowid",0)) or 0)
    audit=log_audit
    def audit_log(self, *, limit=100, stream_id=None):
        sql="SELECT id,time,admin,action,detail,stream_id FROM audit_log"; params=[]
        if stream_id is not None: sql += " WHERE stream_id = "+self.placeholder; params.append(stream_id)
        sql += " ORDER BY id DESC LIMIT "+self.placeholder; params.append(max(1,int(limit))); rows=self._fetch_all(sql,tuple(params))
        for row in rows:
            row["details"]=row["detail"]
            try: row["detail"]=json.loads(row["detail"]); row["details"]=row["detail"]
            except (TypeError,ValueError): pass
        return rows
    def export_streams(self, *, public=False, format="json"):
        if format.lower()!="json": raise ValueError("format must be json")
        return json.dumps(self.list_streams(public=public),indent=2,sort_keys=True)
    def import_streams(self, payload):
        records=json.loads(payload.decode() if isinstance(payload,bytes) else payload) if isinstance(payload,(str,bytes)) else list(payload); out=[]
        for record in records:
            d=dict(record); d.pop("available",None); existing=self.get_by_slug(str(d["slug"]),public=False) if d.get("slug") else None; out.append(self.update_stream(existing["id"],d) if existing else self.create_stream(d))
        return [x for x in out if x is not None]

class SQLiteStore(_SQLStore):
    def __init__(self, path="streams.sqlite3"):
        super().__init__(); p=str(path); p=p[10:] if p.startswith("sqlite:///") else p
        if p!=":memory:": Path(p).expanduser().parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(p,check_same_thread=False); self.connection.row_factory=sqlite3.Row; self._create_tables()
class PostgresStore(_SQLStore):
    backend="postgres"
    def __init__(self,database_url):
        super().__init__()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc: raise RuntimeError("PostgreSQL DATABASE_URL requires the optional psycopg package") from exc
        self.connection=psycopg.connect(database_url,row_factory=dict_row); self._create_tables()
    def _adapt(self,sql): return sql.replace("?","%s").replace("INTEGER PRIMARY KEY","BIGSERIAL PRIMARY KEY")
def create_store(db_path=None, *, database_url=None):
    configured=database_url or os.environ.get("DATABASE_URL") or db_path or os.environ.get("STREAM_DB_PATH","streams.sqlite3"); value=str(configured)
    return PostgresStore(value) if value.startswith(("postgres://","postgresql://")) else SQLiteStore(value)
def create_storage(database_url=None, **kwargs): return create_store(kwargs.pop("path",None),database_url=database_url)
Storage=SQLiteStore
Database=Storage
