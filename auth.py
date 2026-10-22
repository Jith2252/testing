"""Password, session, CSRF, client-IP, and login-failure helpers."""
from __future__ import annotations
import base64, hashlib, hmac, json, math, os, secrets, threading, time
from collections import deque
from typing import Mapping
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN=2**14,8,1,32
def _b64(v): return base64.urlsafe_b64encode(v).decode().rstrip("=")
def _unb64(v): return base64.urlsafe_b64decode(v+"="*(-len(v)%4))
def hash_password(password, *, salt=None, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    if not isinstance(password,str) or not password: raise ValueError("password must not be empty")
    salt=secrets.token_bytes(16) if salt is None else salt; digest=hashlib.scrypt(password.encode(),salt=salt,n=n,r=r,p=p,dklen=SCRYPT_DKLEN); return f"scrypt${_b64(salt)}${_b64(digest)}"
def verify_password(password, encoded):
    try:
        prefix,salt,expected=encoded.split("$",2)
        if prefix!="scrypt": return False
        sb,eb=_unb64(salt),_unb64(expected)
        if len(sb)<16 or len(eb)!=SCRYPT_DKLEN: return False
        return hmac.compare_digest(hashlib.scrypt(str(password).encode(),salt=sb,n=SCRYPT_N,r=SCRYPT_R,p=SCRYPT_P,dklen=SCRYPT_DKLEN),eb)
    except (AttributeError,TypeError,ValueError,IndexError,OverflowError): return False
make_password_hash=hash_password; hash_admin_password=hash_password; verify_password_hash=verify_password
def validate_production_config(config=None, *, require_admin=True):
    values=config if config is not None else os.environ
    if str(values.get("PRODUCTION","0"))!="1": return []
    errors=[]
    if require_admin and not values.get("ADMIN_USER"): errors.append("ADMIN_USER is required when PRODUCTION=1")
    if require_admin and not values.get("ADMIN_PASSWORD_HASH"): errors.append("ADMIN_PASSWORD_HASH is required when PRODUCTION=1")
    elif values.get("ADMIN_PASSWORD_HASH"):
        parts=str(values["ADMIN_PASSWORD_HASH"]).split("$")
        if len(parts)!=3 or parts[0]!="scrypt": errors.append("ADMIN_PASSWORD_HASH must use scrypt$salt$hash format")
    if not values.get("SECRET_KEY"): errors.append("SECRET_KEY is required when PRODUCTION=1")
    elif len(str(values["SECRET_KEY"]))<32: errors.append("SECRET_KEY must be at least 32 characters when PRODUCTION=1")
    return errors
def require_production_config(config=None, *, require_admin=True):
    errors=validate_production_config(config,require_admin=require_admin)
    if errors: raise RuntimeError("; ".join(errors))
def client_ip(headers, peer_ip=None):
    forwarded=headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
    if forwarded:
        vals=[x.strip() for x in forwarded.split(",") if x.strip()]
        if vals: return vals[-1]
    return peer_ip or ""
get_client_ip=client_ip
class IPRateLimiter:
    def __init__(self,max_attempts=5,window_seconds=900,*,clock=time.monotonic): self.max_attempts=int(max_attempts); self.window_seconds=float(window_seconds); self.clock=clock; self._attempts={}; self._lock=threading.Lock(); self.last_forwarded_ip=""
    def _clean(self,ip,now):
        a=self._attempts.setdefault(str(ip),deque())
        while a and a[0]<=now-self.window_seconds: a.popleft()
        return a
    def allow(self,ip,*,now=None):
        now=self.clock() if now is None else float(now)
        with self._lock:
            self.last_forwarded_ip=str(ip); a=self._clean(ip,now)
            if len(a)>=self.max_attempts: return False
            a.append(now); return True
    check=allow
    def retry_after(self,ip,*,now=None):
        now=self.clock() if now is None else float(now)
        with self._lock:
            a=self._clean(ip,now); return max(0,int(math.ceil(a[0]+self.window_seconds-now))) if len(a)>=self.max_attempts else 0
    def retry_info(self,ip,*,now=None):
        now=self.clock() if now is None else float(now); retry=self.retry_after(ip,now=now)
        with self._lock: attempts=len(self._clean(ip,now))
        return {"allowed":retry==0,"retry_after":retry,"attempts":attempts,"ip":str(ip)}
    def reset(self,ip):
        with self._lock: self._attempts.pop(str(ip),None)
class SessionManager:
    def __init__(self,secret=None,*,ttl_seconds=3600,clock=time.time):
        value=secret or os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET")
        if not value: raise ValueError("session secret is required")
        self.secret=value.encode() if isinstance(value,str) else bytes(value); self.ttl_seconds=int(ttl_seconds); self.clock=clock; self._invalidated={}; self._lock=threading.Lock()
    def _sign(self,body): return _b64(hmac.new(self.secret,body.encode(),hashlib.sha256).digest())
    def issue(self,user_id,*,claims=None,ttl_seconds=None):
        now=int(self.clock()); payload={"sub":str(user_id),"iat":now,"exp":now+int(ttl_seconds if ttl_seconds is not None else self.ttl_seconds),"jti":secrets.token_urlsafe(16)}
        if claims:
            if set(claims)&{"sub","iat","exp","jti"}: raise ValueError("claims must not override session fields")
            payload.update(claims)
        body=_b64(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()); return body+"."+self._sign(body)
    create_session=issue
    def verify(self,token):
        if not isinstance(token,str) or token.count(".")!=1: return None
        body,sig=token.split(".",1)
        if not hmac.compare_digest(sig,self._sign(body)): return None
        try: payload=json.loads(_unb64(body)); exp,jti,sub=int(payload["exp"]),str(payload["jti"]),str(payload["sub"])
        except (ValueError,TypeError,KeyError,json.JSONDecodeError): return None
        now=float(self.clock())
        with self._lock:
            self._invalidated={k:v for k,v in self._invalidated.items() if v>now}
            if jti in self._invalidated: return None
        return payload if sub and exp>now else None
    validate=verify
    def logout(self,token):
        payload=self.verify(token)
        if not payload: return False
        with self._lock: self._invalidated[str(payload["jti"])]=float(payload["exp"])
        return True
    invalidate=logout
    def csrf_token(self,session_or_payload):
        payload=self.verify(session_or_payload) if isinstance(session_or_payload,str) else session_or_payload
        if not payload or not payload.get("jti"): raise ValueError("invalid session")
        return _b64(hmac.new(self.secret,("csrf:"+str(payload["jti"])).encode(),hashlib.sha256).digest())
    create_csrf_token=csrf_token
    def verify_csrf(self,session_or_payload,token):
        if not token: return False
        try: return hmac.compare_digest(self.csrf_token(session_or_payload),token)
        except ValueError: return False
    validate_csrf=verify_csrf
Session=SessionManager; RateLimiter=IPRateLimiter
