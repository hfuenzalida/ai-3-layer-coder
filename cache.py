"""
Codex Futura — Response Cache
SQLite-backed cache keyed on sha256(model + prompt).
Per-tier TTL: Light=24h, Heavy=7d, Orchestrator=3d.
"""
import sqlite3
import hashlib
import threading
import time
import os
import config

_DB_PATH      = os.path.join(config.BASE_DIR, "response_cache.db")
_thread_local = threading.local()

# Per-process hit/miss counters — reset each time the process starts.
# Gives the `cache` REPL command a live hit-rate for the current session.
_hits:   int = 0
_misses: int = 0

_TTL = {
    "Light":        86_400,     # 24 hours  — local model, fast to re-run anyway
    "Heavy":        604_800,    # 7 days    — cloud coding model, expensive
    "Orchestrator": 259_200,    # 3 days    — strategic, may go stale faster
}
_DEFAULT_TTL = 86_400


def _get_conn() -> sqlite3.Connection:
    """
    Return a per-thread SQLite connection, creating it on first use.
    Schema creation runs once per thread, not on every operation.
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        os.makedirs(config.BASE_DIR, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                tier       TEXT NOT NULL,
                model      TEXT NOT NULL,
                content    TEXT NOT NULL,
                tokens     INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON cache (created_at)")
        conn.commit()
        _thread_local.conn = conn
    return conn


def _key(prompt: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x00{prompt}".encode()).hexdigest()


def get(prompt: str, model: str, tier: str) -> dict | None:
    """Return cached result or None on miss / expiry."""
    global _hits, _misses
    ttl = _TTL.get(tier, _DEFAULT_TTL)
    try:
        conn = _get_conn()
        row  = conn.execute(
            "SELECT content, tokens, model FROM cache WHERE key = ? AND created_at > ?",
            (_key(prompt, model), time.time() - ttl),
        ).fetchone()
        if row:
            _hits += 1
            return {"content": row[0], "tokens": row[1], "model": row[2]}
    except Exception:
        pass
    _misses += 1
    return None


def put(prompt: str, model: str, tier: str, content: str, tokens: int) -> None:
    """Store a response. Silently ignores errors so a cache failure never blocks execution."""
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO cache
               (key, tier, model, content, tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_key(prompt, model), tier, model, content, tokens, time.time()),
        )
        conn.commit()
    except Exception:
        pass


def stats() -> dict:
    """Return size info and session hit-rate for the cache command."""
    try:
        conn    = _get_conn()
        total   = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        by_tier = conn.execute(
            "SELECT tier, COUNT(*), SUM(tokens) FROM cache GROUP BY tier"
        ).fetchall()
        lookups  = _hits + _misses
        hit_rate = (_hits / lookups) if lookups > 0 else None
        return {
            "total":    total,
            "by_tier":  by_tier,
            "hits":     _hits,
            "misses":   _misses,
            "hit_rate": hit_rate,   # float 0.0–1.0, or None if no lookups yet
        }
    except Exception:
        return {"total": 0, "by_tier": [], "hits": 0, "misses": 0, "hit_rate": None}


def clear_expired() -> int:
    """Purge stale entries. Returns number of rows deleted."""
    deleted = 0
    try:
        conn = _get_conn()
        for tier, ttl in _TTL.items():
            cur      = conn.execute(
                "DELETE FROM cache WHERE tier = ? AND created_at < ?",
                (tier, time.time() - ttl),
            )
            deleted += cur.rowcount
        conn.commit()
    except Exception:
        pass
    return deleted


def clear_all() -> int:
    """Wipe entire cache. Returns rows deleted."""
    try:
        conn = _get_conn()
        cur  = conn.execute("DELETE FROM cache")
        conn.commit()
        return cur.rowcount
    except Exception:
        return 0
