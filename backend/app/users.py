"""Local user accounts (roles: admin, user). Passwords are stored as salted scrypt hashes."""
import hashlib
import hmac
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

from fastapi import HTTPException

from . import config

_lock = threading.Lock()
ROLES = ("admin", "user")
_NAME = re.compile(r"^[a-z0-9._-]{2,40}$")


def _conn():
    c = sqlite3.connect(config.DATA_DIR / "users.db", timeout=15)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pw TEXT NOT NULL, role TEXT NOT NULL, "
        "active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, created TEXT, last_login TEXT)"
    )
    return c


def _hash(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def _check(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public(r) -> dict:
    return {"username": r["username"], "role": r["role"], "active": bool(r["active"]),
            "must_change": bool(r["must_change"]), "created": r["created"], "last_login": r["last_login"]}


def ensure_admin():
    """First start: create the 'admin' account (password from PDFDESK_ADMIN_PASSWORD) and force a change at first sign-in."""
    with _lock:
        c = _conn()
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            c.execute("INSERT OR IGNORE INTO users (username,pw,role,active,must_change,created) VALUES (?,?,?,?,?,?)",
                      ("admin", _hash(config.ADMIN_PASSWORD), "admin", 1, 1, _now()))  # IGNORE: several workers start at once
            c.commit()
        c.close()


def _validate_password(pw: str):
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")


def verify(username: str, password: str):
    with _lock:
        c = _conn()
        r = c.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        ok = bool(r and r["active"] and _check(password, r["pw"]))
        if ok:
            c.execute("UPDATE users SET last_login=? WHERE username=?", (_now(), r["username"]))
            c.commit()
        c.close()
    return _public(r) if ok else None


def get(username: str):
    with _lock:
        c = _conn()
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        c.close()
    return _public(r) if r else None


def list_all() -> list[dict]:
    with _lock:
        c = _conn()
        rows = c.execute("SELECT * FROM users ORDER BY username").fetchall()
        c.close()
    return [_public(r) for r in rows]


def _admins_left(c, excluding: str) -> int:
    return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND username<>?", (excluding,)).fetchone()[0]


def create(username: str, password: str, role: str) -> dict:
    username = username.strip().lower()
    if not _NAME.match(username):
        raise HTTPException(400, "Username: 2-40 characters, letters, numbers, dot, dash or underscore")
    if role not in ROLES:
        raise HTTPException(400, "Role must be admin or user")
    _validate_password(password)
    with _lock:
        c = _conn()
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            c.close()
            raise HTTPException(400, "That username already exists")
        c.execute("INSERT INTO users (username,pw,role,active,must_change,created) VALUES (?,?,?,?,?,?)",
                  (username, _hash(password), role, 1, 1, _now()))
        c.commit()
        c.close()
    return get(username)


def update(username: str, actor: str, role: str | None = None, active: bool | None = None, password: str | None = None) -> dict:
    with _lock:
        c = _conn()
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not r:
            c.close()
            raise HTTPException(404, "No such user")
        if role is not None and role not in ROLES:
            c.close()
            raise HTTPException(400, "Role must be admin or user")
        loses_admin = r["role"] == "admin" and r["active"] and ((role and role != "admin") or active is False)
        if loses_admin and _admins_left(c, username) == 0:
            c.close()
            raise HTTPException(400, "There must be at least one active admin")
        if username == actor and (active is False):
            c.close()
            raise HTTPException(400, "You can't disable your own account")
        if role is not None:
            c.execute("UPDATE users SET role=? WHERE username=?", (role, username))
        if active is not None:
            c.execute("UPDATE users SET active=? WHERE username=?", (1 if active else 0, username))
        if password is not None:
            _validate_password(password)
            c.execute("UPDATE users SET pw=?, must_change=1 WHERE username=?", (_hash(password), username))
        c.commit()
        c.close()
    return get(username)


def delete(username: str, actor: str):
    with _lock:
        c = _conn()
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not r:
            c.close()
            raise HTTPException(404, "No such user")
        if username == actor:
            c.close()
            raise HTTPException(400, "You can't delete your own account")
        if r["role"] == "admin" and r["active"] and _admins_left(c, username) == 0:
            c.close()
            raise HTTPException(400, "There must be at least one active admin")
        c.execute("DELETE FROM users WHERE username=?", (username,))
        c.commit()
        c.close()


def change_own_password(username: str, old: str, new: str):
    with _lock:
        c = _conn()
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not r or not _check(old, r["pw"]):
            c.close()
            raise HTTPException(400, "Current password is wrong")
        _validate_password(new)
        if old == new:
            c.close()
            raise HTTPException(400, "Choose a different password")
        c.execute("UPDATE users SET pw=?, must_change=0 WHERE username=?", (_hash(new), username))
        c.commit()
        c.close()
