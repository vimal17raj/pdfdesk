import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.execute(
        "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
        "user TEXT, action TEXT, doc_id TEXT, detail TEXT, ip TEXT)"
    )
    return c


def log(user: str, action: str, doc_id: str = "", detail: str = "", ip: str = ""):
    with _lock:
        c = _conn()
        c.execute(
            "INSERT INTO audit (ts,user,action,doc_id,detail,ip) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), user, action, doc_id, detail, ip),
        )
        c.commit()
        c.close()


def query(limit: int = 500, user: str = "", action: str = ""):
    sql, args = "SELECT ts,user,action,doc_id,detail,ip FROM audit WHERE 1=1", []
    if user:
        sql += " AND user LIKE ?"
        args.append(f"%{user}%")
    if action:
        sql += " AND action=?"
        args.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        c = _conn()
        rows = c.execute(sql, args).fetchall()
        c.close()
    keys = ("ts", "user", "action", "doc_id", "detail", "ip")
    return [dict(zip(keys, r)) for r in rows]
