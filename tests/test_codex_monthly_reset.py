"""
Tests for codex_monthly_reset.py — generate_monthly_summary, compress,
purge, run_maintenance, and run_reset.
All tests use tmp_path so the real ~/codex_futura/ tree is never touched.
"""
import csv
import gzip
import os
from datetime import datetime, timedelta

import pytest
import config
import codex_monthly_reset as cmr


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_log(path: str, rows: list[dict]) -> None:
    """Write a minimal usage CSV at `path`."""
    header = ["timestamp", "model", "provider", "tier", "tokens", "estimated_cost", "status", "task_summary"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in header}, **r})


def _make_df(rows: list[dict]):
    import pandas as pd
    return pd.DataFrame(rows)


# ── generate_monthly_summary ───────────────────────────────────────────────────

def test_summary_contains_totals():
    import pandas as pd
    df = pd.DataFrame([
        {"tokens": 1000, "estimated_cost": "$0.1000"},
        {"tokens": 2000, "estimated_cost": "$0.2000"},
    ])
    summary = cmr.generate_monthly_summary(df)
    assert "3,000" in summary        # total tokens
    assert "0.3000" in summary       # total USD
    assert "MONTHLY CLOSURE" in summary


def test_summary_zero_spend():
    import pandas as pd
    df = pd.DataFrame([{"tokens": 500, "estimated_cost": "$0.0000"}])
    summary = cmr.generate_monthly_summary(df)
    assert "500" in summary
    assert "$0.0000" in summary


def test_summary_includes_clp(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(config, "USD_TO_CLP", 1000.0)
    df = pd.DataFrame([{"tokens": 100, "estimated_cost": "$1.0000"}])
    summary = cmr.generate_monthly_summary(df)
    assert "1,000" in summary   # 1 USD × 1000 = 1,000 CLP


# ── _compress_old_archives ─────────────────────────────────────────────────────

def test_compress_old_csv(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    old_csv = archive / "usage_2024_01.csv"
    old_csv.write_text("timestamp,model\n2024-01-01,m\n")
    # Back-date mtime to 4 months ago
    old_time = (datetime.now() - timedelta(days=120)).timestamp()
    os.utime(str(old_csv), (old_time, old_time))

    compressed = cmr._compress_old_archives(str(archive), months=3)
    assert "usage_2024_01.csv" in compressed
    assert not old_csv.exists()
    assert (archive / "usage_2024_01.csv.gz").exists()


def test_compress_recent_csv_not_touched(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    recent = archive / "usage_2026_03.csv"
    recent.write_text("timestamp,model\n")
    # mtime is now → less than 3 months old

    compressed = cmr._compress_old_archives(str(archive), months=3)
    assert compressed == []
    assert recent.exists()


def test_compressed_file_is_valid_gzip(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    old_csv = archive / "usage_old.csv"
    old_csv.write_text("timestamp,model\n2023-01-01,m\n")
    old_time = (datetime.now() - timedelta(days=200)).timestamp()
    os.utime(str(old_csv), (old_time, old_time))

    cmr._compress_old_archives(str(archive), months=3)
    gz = archive / "usage_old.csv.gz"
    with gzip.open(str(gz), "rb") as f:
        content = f.read().decode()
    assert "timestamp" in content


# ── _purge_old_archives ────────────────────────────────────────────────────────

def test_purge_old_csv(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    old = archive / "usage_2020_01.csv"
    old.write_text("data")
    old_time = (datetime.now() - timedelta(days=800)).timestamp()
    os.utime(str(old), (old_time, old_time))

    deleted = cmr._purge_old_archives(str(archive), years=2)
    assert "usage_2020_01.csv" in deleted
    assert not old.exists()


def test_purge_old_gz(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    old = archive / "usage_2020_01.csv.gz"
    old.write_bytes(b"gzdata")
    old_time = (datetime.now() - timedelta(days=800)).timestamp()
    os.utime(str(old), (old_time, old_time))

    deleted = cmr._purge_old_archives(str(archive), years=2)
    assert "usage_2020_01.csv.gz" in deleted


def test_purge_recent_not_deleted(tmp_path):
    archive = tmp_path / "archives"
    archive.mkdir()
    recent = archive / "usage_2025_01.csv"
    recent.write_text("data")
    # mtime is now — well under 2 years

    deleted = cmr._purge_old_archives(str(archive), years=2)
    assert deleted == []
    assert recent.exists()


# ── run_maintenance ────────────────────────────────────────────────────────────

def test_run_maintenance_no_archive_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path / "no_such_dir"))
    result = cmr.run_maintenance(verbose=False)
    assert result == {"compressed": [], "deleted": []}


def test_run_maintenance_returns_dict(tmp_path, monkeypatch):
    archive = tmp_path / "archives"
    archive.mkdir()
    monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))
    result = cmr.run_maintenance(verbose=False)
    assert "compressed" in result
    assert "deleted"    in result


# ── run_reset ──────────────────────────────────────────────────────────────────

def test_run_reset_no_log_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "LOG_FILE",        str(tmp_path / "no_log.csv"))
    monkeypatch.setattr(config, "ARCHIVE_DIR",     str(tmp_path / "archives"))
    monkeypatch.setattr(config, "LAST_RESET_FILE", str(tmp_path / "last_reset.txt"))
    cmr.run_reset(run_maintenance_after=False)
    captured = capsys.readouterr()
    assert "No active log" in captured.out


def test_run_reset_moves_log_to_archive(tmp_path, monkeypatch):
    log = str(tmp_path / "model_usage_2026.csv")
    archive_dir = str(tmp_path / "archives")
    stamp = str(tmp_path / "last_reset.txt")
    _write_log(log, [{"tokens": "500", "estimated_cost": "$0.0700"}])
    monkeypatch.setattr(config, "LOG_FILE",        log)
    monkeypatch.setattr(config, "ARCHIVE_DIR",     archive_dir)
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)

    cmr.run_reset(run_maintenance_after=False)

    assert not os.path.exists(log) or open(log).readline().startswith("timestamp")
    archives = os.listdir(archive_dir)
    csv_archives = [f for f in archives if f.endswith(".csv")]
    assert len(csv_archives) >= 1


def test_run_reset_creates_fresh_log_with_header(tmp_path, monkeypatch):
    log = str(tmp_path / "model_usage_2026.csv")
    archive_dir = str(tmp_path / "archives")
    stamp = str(tmp_path / "last_reset.txt")
    _write_log(log, [{"tokens": "100", "estimated_cost": "$0.0100"}])
    monkeypatch.setattr(config, "LOG_FILE",        log)
    monkeypatch.setattr(config, "ARCHIVE_DIR",     archive_dir)
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)

    cmr.run_reset(run_maintenance_after=False)

    assert os.path.exists(log)
    first_line = open(log).readline().strip()
    assert first_line == "timestamp,model,provider,tier,tokens,estimated_cost,status,task_summary"


def test_run_reset_writes_stamp(tmp_path, monkeypatch):
    log = str(tmp_path / "model_usage_2026.csv")
    archive_dir = str(tmp_path / "archives")
    stamp = str(tmp_path / "last_reset.txt")
    _write_log(log, [{"tokens": "100", "estimated_cost": "$0.0000"}])
    monkeypatch.setattr(config, "LOG_FILE",        log)
    monkeypatch.setattr(config, "ARCHIVE_DIR",     archive_dir)
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)

    cmr.run_reset(run_maintenance_after=False)

    assert os.path.exists(stamp)
    content = open(stamp).read()
    # Should be a valid ISO timestamp
    datetime.fromisoformat(content)   # raises if invalid


def test_run_reset_writes_report_txt(tmp_path, monkeypatch):
    log = str(tmp_path / "model_usage_2026.csv")
    archive_dir = str(tmp_path / "archives")
    stamp = str(tmp_path / "last_reset.txt")
    _write_log(log, [{"tokens": "200", "estimated_cost": "$0.0200"}])
    monkeypatch.setattr(config, "LOG_FILE",        log)
    monkeypatch.setattr(config, "ARCHIVE_DIR",     archive_dir)
    monkeypatch.setattr(config, "LAST_RESET_FILE", stamp)

    cmr.run_reset(run_maintenance_after=False)

    txts = [f for f in os.listdir(archive_dir) if f.endswith(".txt")]
    assert len(txts) == 1
    content = open(os.path.join(archive_dir, txts[0])).read()
    assert "MONTHLY CLOSURE" in content
