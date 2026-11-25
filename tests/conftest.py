"""
Codex Futura — Shared Test Infrastructure
------------------------------------------
This conftest.py runs BEFORE any test file is collected or imported.
It redirects all config paths to a temporary directory so tests never
touch the real ~/codex_futura/ tree and module-level side-effects
(CSV creation, DB setup, circuit breaker load) all land safely in tmp.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── Path bootstrap ─────────────────────────────────────────────────────────────
# Insert project root so 'import config' etc. work without installation.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Redirect ALL config paths to a throwaway temp tree ────────────────────────
# This must happen BEFORE any project module is imported so that modules
# which capture paths at import time (cache._DB_PATH, orchestrator._CIRCUIT_STATE_FILE)
# receive the test-safe values.

_TEST_BASE = tempfile.mkdtemp(prefix="codex_test_")

import config  # noqa: E402 — must be after sys.path but before all other project imports

config.BASE_DIR          = _TEST_BASE
config.LOG_FILE          = os.path.join(_TEST_BASE, "model_usage_test.csv")
config.ARCHIVE_DIR       = os.path.join(_TEST_BASE, "archives")
config.LOCK_FILE         = os.path.join(_TEST_BASE, "budget.lock")
config.SOFT_LOCK_FILE    = os.path.join(_TEST_BASE, "budget_soft.lock")
config.MONITOR_CURRENT   = os.path.join(_TEST_BASE, "monitor_current.json")
config.SESSION_FILE      = os.path.join(_TEST_BASE, "current_session.json")
config.LAST_RESET_FILE   = os.path.join(_TEST_BASE, "last_reset.txt")
config.DETECTIVE_LOG     = os.path.join(_TEST_BASE, "detective_log.jsonl")
config.OUTPUT_DIR        = os.path.join(_TEST_BASE, "output")
config.TEMPLATES_DIR     = os.path.join(_TEST_BASE, "templates")
config.OPENROUTER_API_KEY = "test-key-not-real"

os.makedirs(config.OUTPUT_DIR,   exist_ok=True)
os.makedirs(config.TEMPLATES_DIR, exist_ok=True)

# Patch cache DB path and reset its thread-local + counters
import cache  # noqa: E402

cache._DB_PATH = os.path.join(_TEST_BASE, "test_cache.db")

# Patch orchestrator's circuit-breaker file path (set at module import time)
import orchestrator  # noqa: E402

orchestrator._CIRCUIT_STATE_FILE = os.path.join(_TEST_BASE, "circuit_breaker.json")


# ── Per-test cache isolation ───────────────────────────────────────────────────

@pytest.fixture
def clean_cache(tmp_path):
    """
    Give each test its own fresh SQLite DB and reset the hit/miss counters.
    Clears the thread-local connection so _get_conn() opens the new file.
    """
    import cache as _cache
    db_file = str(tmp_path / "cache_test.db")
    old_path = _cache._DB_PATH
    _cache._DB_PATH = db_file

    # Drop any existing thread-local connection so the new path is used
    if hasattr(_cache._thread_local, "conn"):
        try:
            _cache._thread_local.conn.close()
        except Exception:
            pass
        del _cache._thread_local.conn

    # Reset counters
    _cache._hits   = 0
    _cache._misses = 0

    yield _cache

    # Teardown
    _cache._DB_PATH = old_path
    if hasattr(_cache._thread_local, "conn"):
        try:
            _cache._thread_local.conn.close()
        except Exception:
            pass
        del _cache._thread_local.conn
    _cache._hits   = 0
    _cache._misses = 0


# ── Per-test circuit-breaker isolation ────────────────────────────────────────

@pytest.fixture
def reset_circuit():
    """Reset the circuit-breaker module globals before and after each test."""
    import orchestrator as _orch
    _orch._local_failure_count    = 0
    _orch._local_circuit_open     = False
    _orch._local_circuit_open_until = 0.0
    yield _orch
    _orch._local_failure_count    = 0
    _orch._local_circuit_open     = False
    _orch._local_circuit_open_until = 0.0


# ── Lock file helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def no_locks(tmp_path):
    """Ensure hard and soft lock files do NOT exist for the duration of a test."""
    for p in (config.LOCK_FILE, config.SOFT_LOCK_FILE):
        if os.path.exists(p):
            os.remove(p)
    yield
    for p in (config.LOCK_FILE, config.SOFT_LOCK_FILE):
        if os.path.exists(p):
            os.remove(p)


# ── Temp log file ──────────────────────────────────────────────────────────────

@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    """Point config.LOG_FILE at a fresh temp CSV for a single test."""
    log = str(tmp_path / "test_usage.csv")
    monkeypatch.setattr(config, "LOG_FILE", log)
    yield log
