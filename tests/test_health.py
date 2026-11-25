"""
Tests for health.py — budget lock probes, .gitignore check, reset overdue check.
All tests use temporary file paths; no real ~/codex_futura/ files are touched.
"""
import os
from datetime import datetime, timedelta

import pytest
import config
from health import check_budget_lock, check_gitignore, check_reset_overdue


# ── check_budget_lock ──────────────────────────────────────────────────────────

def test_no_lock_files_returns_clear(no_locks):
    locked, msg = check_budget_lock()
    assert locked is False
    assert "clear" in msg.lower() or "ok" in msg.lower() or msg == "Clear"


def test_hard_lock_file_detected(no_locks):
    open(config.LOCK_FILE, "w").close()
    locked, msg = check_budget_lock()
    assert locked is True
    assert "hard" in msg.lower() or "lock" in msg.lower()
    os.remove(config.LOCK_FILE)


def test_soft_lock_file_detected(no_locks):
    open(config.SOFT_LOCK_FILE, "w").close()
    locked, msg = check_budget_lock()
    assert locked is True
    assert "soft" in msg.lower() or "80" in msg
    os.remove(config.SOFT_LOCK_FILE)


def test_hard_lock_takes_priority_over_soft(no_locks):
    open(config.LOCK_FILE,      "w").close()
    open(config.SOFT_LOCK_FILE, "w").close()
    locked, msg = check_budget_lock()
    assert locked is True
    assert "hard" in msg.lower()
    os.remove(config.LOCK_FILE)
    os.remove(config.SOFT_LOCK_FILE)


# ── check_gitignore ────────────────────────────────────────────────────────────

def test_gitignore_missing_returns_exposed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exposed, msg = check_gitignore()
    assert exposed is True
    assert "missing" in msg.lower() or "gitignore" in msg.lower()


def test_gitignore_with_dot_env_returns_protected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n__pycache__/\n")
    exposed, msg = check_gitignore()
    assert exposed is False
    assert "protected" in msg.lower()


def test_gitignore_with_wildcard_star_env_returns_protected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("*.env\n")
    exposed, msg = check_gitignore()
    assert exposed is False


def test_gitignore_with_env_dotstar_returns_protected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(".env.*\n")
    exposed, msg = check_gitignore()
    assert exposed is False


def test_gitignore_without_env_entry_returns_exposed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\n")
    exposed, msg = check_gitignore()
    assert exposed is True
    assert "not listed" in msg.lower() or "missing" in msg.lower() or "not" in msg.lower()


def test_gitignore_comment_lines_ignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("# This is a comment\n# .env\n*.pyc\n")
    exposed, msg = check_gitignore()
    assert exposed is True  # comment lines should NOT count


# ── check_reset_overdue ────────────────────────────────────────────────────────

def test_no_reset_file_and_no_log_returns_not_overdue(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LAST_RESET_FILE", str(tmp_path / "last_reset.txt"))
    monkeypatch.setattr(config, "LOG_FILE",        str(tmp_path / "no_log.csv"))
    overdue, msg = check_reset_overdue()
    assert overdue is False


def test_recent_reset_stamp_returns_not_overdue(monkeypatch, tmp_path):
    stamp = str(tmp_path / "last_reset.txt")
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)
    with open(stamp, "w") as f:
        f.write((datetime.now() - timedelta(days=10)).isoformat())
    overdue, msg = check_reset_overdue()
    assert overdue is False
    assert "10" in msg or "d ago" in msg


def test_old_reset_stamp_returns_overdue(monkeypatch, tmp_path):
    stamp = str(tmp_path / "last_reset.txt")
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)
    with open(stamp, "w") as f:
        f.write((datetime.now() - timedelta(days=40)).isoformat())
    overdue, msg = check_reset_overdue()
    assert overdue is True
    assert "reset" in msg.lower()


def test_boundary_exactly_35_days_not_overdue(monkeypatch, tmp_path):
    stamp = str(tmp_path / "last_reset.txt")
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)
    with open(stamp, "w") as f:
        f.write((datetime.now() - timedelta(days=35)).isoformat())
    overdue, _ = check_reset_overdue()
    assert overdue is False   # exactly 35 is within the grace window


def test_boundary_36_days_is_overdue(monkeypatch, tmp_path):
    stamp = str(tmp_path / "last_reset.txt")
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)
    with open(stamp, "w") as f:
        f.write((datetime.now() - timedelta(days=36)).isoformat())
    overdue, _ = check_reset_overdue()
    assert overdue is True


def test_falls_back_to_log_file_ctime(monkeypatch, tmp_path):
    """When no stamp file exists, ctime of the log file is used as proxy."""
    monkeypatch.setattr(config, "LAST_RESET_FILE", str(tmp_path / "no_stamp.txt"))
    log = str(tmp_path / "model_usage.csv")
    open(log, "w").close()
    monkeypatch.setattr(config, "LOG_FILE", log)
    # A freshly created file is 0 days old — should not be overdue
    overdue, _ = check_reset_overdue()
    assert overdue is False
