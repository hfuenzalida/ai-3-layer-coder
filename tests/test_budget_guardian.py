"""
Tests for budget_guardian.py — _evaluate_budget, _read_log_safe,
check_budget, and the graceful plyer degradation.
All file paths are redirected to tmp_path; no real lock files are created.
"""
import csv
import os
import time

import pytest
import config
import budget_guardian as bg


# ── _notify graceful degradation ───────────────────────────────────────────────

def test_notify_without_plyer_does_not_raise(monkeypatch):
    """Even with plyer unavailable, _notify must not raise."""
    monkeypatch.setattr(bg, "_PLYER_AVAILABLE", False)
    monkeypatch.setattr(bg, "_plyer_notification", None)
    bg._notify("Test title", "Test message")   # must not raise


def test_notify_with_plyer_calls_notify(monkeypatch):
    """When plyer is available, _notify must call notification.notify."""
    from unittest.mock import MagicMock
    mock_notif = MagicMock()
    monkeypatch.setattr(bg, "_PLYER_AVAILABLE", True)
    monkeypatch.setattr(bg, "_plyer_notification", mock_notif)
    bg._notify("Title", "Msg", timeout=5)
    mock_notif.notify.assert_called_once()


def test_notify_plyer_backend_crash_does_not_raise(monkeypatch):
    """If notification.notify raises, _notify must swallow it."""
    from unittest.mock import MagicMock
    mock_notif = MagicMock()
    mock_notif.notify.side_effect = Exception("display unavailable")
    monkeypatch.setattr(bg, "_PLYER_AVAILABLE", True)
    monkeypatch.setattr(bg, "_plyer_notification", mock_notif)
    bg._notify("T", "M")   # must not propagate the exception


# ── _evaluate_budget ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_locks(no_locks):
    """Ensure lock files don't exist and reset guardian cache globals."""
    bg._last_mtime       = 0.0
    bg._cached_total_clp = 0.0
    yield
    bg._last_mtime       = 0.0
    bg._cached_total_clp = 0.0


def test_evaluate_below_soft_threshold_no_locks(monkeypatch):
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP",      10_000.0)
    monkeypatch.setattr(config, "BUDGET_SOFT_THRESHOLD", 0.80)
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)
    bg._evaluate_budget(5_000.0)   # 50% — below soft threshold
    assert not os.path.exists(config.LOCK_FILE)
    assert not os.path.exists(config.SOFT_LOCK_FILE)


def test_evaluate_at_soft_threshold_creates_soft_lock(monkeypatch):
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP",      10_000.0)
    monkeypatch.setattr(config, "BUDGET_SOFT_THRESHOLD", 0.80)
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)
    bg._evaluate_budget(8_500.0)   # 85% — above soft threshold
    assert os.path.exists(config.SOFT_LOCK_FILE)
    assert not os.path.exists(config.LOCK_FILE)


def test_evaluate_at_hard_limit_creates_hard_lock(monkeypatch):
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP",      10_000.0)
    monkeypatch.setattr(config, "BUDGET_SOFT_THRESHOLD", 0.80)
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)
    bg._evaluate_budget(10_000.0)   # exactly at limit
    assert os.path.exists(config.LOCK_FILE)


def test_evaluate_hard_lock_not_duplicated(monkeypatch):
    """_evaluate_budget must not call _notify again if lock file already exists."""
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP",      10_000.0)
    monkeypatch.setattr(config, "BUDGET_SOFT_THRESHOLD", 0.80)
    call_count = [0]
    def counting_notify(*a, **k):
        call_count[0] += 1
    monkeypatch.setattr(bg, "_notify", counting_notify)
    # First call — creates lock + notifies
    bg._evaluate_budget(10_500.0)
    # Second call — lock already exists → must NOT notify again
    bg._evaluate_budget(10_500.0)
    assert call_count[0] == 1


def test_evaluate_below_soft_removes_soft_lock(monkeypatch):
    """If spend drops below soft threshold, soft lock should be removed."""
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP",      10_000.0)
    monkeypatch.setattr(config, "BUDGET_SOFT_THRESHOLD", 0.80)
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)
    # Create soft lock manually
    open(config.SOFT_LOCK_FILE, "w").close()
    bg._evaluate_budget(3_000.0)   # well below threshold
    assert not os.path.exists(config.SOFT_LOCK_FILE)


# ── _read_log_safe ─────────────────────────────────────────────────────────────

def test_read_log_safe_valid_csv(tmp_path, monkeypatch):
    log = str(tmp_path / "usage.csv")
    with open(log, "w", newline="") as f:
        csv.writer(f).writerows([
            ["timestamp", "model", "provider", "tier", "tokens", "estimated_cost", "status", "task_summary"],
            ["2026-01-01", "m", "p", "Heavy", "100", "$0.01", "Success", "t"],
        ])
    monkeypatch.setattr(config, "LOG_FILE", log)
    df = bg._read_log_safe()
    assert not df.empty
    assert "estimated_cost" in df.columns


def test_read_log_safe_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "no_file.csv"))
    df = bg._read_log_safe()
    assert df.empty


def test_read_log_safe_malformed_csv_returns_empty(tmp_path, monkeypatch):
    log = str(tmp_path / "bad.csv")
    open(log, "w").write("not,a,valid\ncsv file without proper columns\n")
    monkeypatch.setattr(config, "LOG_FILE", log)
    df = bg._read_log_safe()
    # Should return empty since 'estimated_cost' column is absent
    assert df.empty or "estimated_cost" not in df.columns


# ── check_budget ───────────────────────────────────────────────────────────────

def test_check_budget_no_log_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "no_file.csv"))
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)
    bg.check_budget()   # must not raise
    assert not os.path.exists(config.LOCK_FILE)


def test_check_budget_uses_mtime_cache(tmp_path, monkeypatch):
    """If mtime hasn't changed, _evaluate_budget is called with cached value (no pandas re-read)."""
    log = str(tmp_path / "usage.csv")
    with open(log, "w", newline="") as f:
        csv.writer(f).writerows([
            ["timestamp", "model", "provider", "tier", "tokens", "estimated_cost", "status", "task_summary"],
            ["2026-01-01", "m", "p", "Heavy", "100", "$0.01", "Success", "t"],
        ])
    monkeypatch.setattr(config, "LOG_FILE", log)
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP", 999_999.0)
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)

    # First call populates the cache
    bg.check_budget()
    first_mtime = bg._last_mtime
    cached_clp  = bg._cached_total_clp

    # Second call — mtime same → should reuse cached value
    pandas_calls = [0]
    original_read = bg._read_log_safe
    def counting_read():
        pandas_calls[0] += 1
        return original_read()
    monkeypatch.setattr(bg, "_read_log_safe", counting_read)
    bg.check_budget()
    assert pandas_calls[0] == 0   # pandas NOT called again


def test_check_budget_hard_lock_on_overspend(tmp_path, monkeypatch):
    log = str(tmp_path / "usage.csv")
    with open(log, "w", newline="") as f:
        csv.writer(f).writerows([
            ["timestamp", "model", "provider", "tier", "tokens", "estimated_cost", "status", "task_summary"],
            ["2026-01-01", "m", "p", "Heavy", "100", "$100.00", "Success", "task"],
        ])
    monkeypatch.setattr(config, "LOG_FILE",        log)
    monkeypatch.setattr(config, "BUDGET_LIMIT_CLP", 1.0)      # tiny limit
    monkeypatch.setattr(config, "EXCHANGE_RATE",    1.0)       # 1:1 to simplify
    monkeypatch.setattr(bg, "_notify", lambda *a, **k: None)

    bg.check_budget()
    assert os.path.exists(config.LOCK_FILE)
