import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config, users

bearer = HTTPBearer(auto_error=False)

_fails: dict[str, list[float]] = {}
_MAX_FAILS, _WINDOW = 5, 300


def authenticate(username: str, password: str, ip: str = ""):
    """Returns the user record or None. Five wrong passwords in 5 minutes locks that name+address for a while."""
    key = f"{ip}|{username.strip().lower()}"
    now = time.time()
    recent = [t for t in _fails.get(key, []) if now - t < _WINDOW]
    if len(recent) >= _MAX_FAILS:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    u = users.verify(username, password) if username.strip() and password else None
    if not u:
        _fails[key] = recent + [now]
        return None
    _fails.pop(key, None)
    return u


def make_token(username: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=config.JWT_HOURS)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, config.JWT_SECRET, algorithm="HS256")


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP; the port is bound to localhost so clients can't reach the app directly
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> dict:
    token = creds.credentials if creds else request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        data = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    rec = users.get(data["sub"])  # role / disabled status are re-checked on every request
    if not rec or not rec["active"]:
        raise HTTPException(401, "Account disabled or removed")
    return {"user": rec["username"], "role": rec["role"], "must_change": rec["must_change"], "ip": client_ip(request)}


def admin_only(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Admin only")
    return u
