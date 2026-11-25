"""
Tests for codex_logger.py — log_task validation and CSV writing.
Uses temp_log fixture so tests never touch the real monthly log.
"""
import csv
import math
import os
import threading

import pytest
import config


# ── cost_override validation ───────────────────────────────────────────────────

def test_log_task_valid_inputs_writes_row(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(config, "LOG_FILE", temp_log)
    # Reset the module-level LOG_FILE reference inside codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)

    result = codex_logger.log_task(
        model="qwen/qwen-2.5-coder-32b-instruct",
        tier="Heavy",
        tokens=500,
        task="Write a sorting function",
        status="Success",
    )
    assert "Logged" in result

    with open(temp_log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["tier"] == "Heavy"
    assert rows[0]["tokens"] == "500"
    assert rows[0]["status"] == "Success"


def test_log_task_light_tier_costs_zero(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)

    codex_logger.log_task(
        model="qwen2.5-coder:3b",
        tier="Light",
        tokens=1000,
        task="Format docstrings",
    )
    with open(temp_log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["estimated_cost"] == "$0.0000"


def test_log_task_cost_override_valid(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)

    result = codex_logger.log_task(
        model="m", tier="Heavy", tokens=100, task="t",
        cost_override=0.05,
    )
    assert "Error" not in result
    with open(temp_log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert "$0.0500" in rows[0]["estimated_cost"]


def test_log_task_cost_override_zero_is_valid(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)
    result = codex_logger.log_task("m", "Heavy", 100, "t", cost_override=0.0)
    assert "Error" not in result


def test_log_task_cost_override_negative_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=-0.01)
    assert "Error" in result
    assert "negative" in result.lower()


def test_log_task_cost_override_nan_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=float("nan"))
    assert "Error" in result
    assert "finite" in result.lower()


def test_log_task_cost_override_inf_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=float("inf"))
    assert "Error" in result
    assert "finite" in result.lower()


def test_log_task_cost_override_negative_inf_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=float("-inf"))
    assert "Error" in result


def test_log_task_cost_override_exceeds_cap_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=150.0)
    assert "Error" in result
    assert "cap" in result.lower() or "exceeds" in result.lower()


def test_log_task_cost_override_at_cap_accepted(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)
    result = codex_logger.log_task("m", "Heavy", 100, "t", cost_override=100.0)
    assert "Error" not in result


def test_log_task_cost_override_just_above_cap_rejected():
    from codex_logger import log_task
    result = log_task("m", "Heavy", 100, "t", cost_override=100.01)
    assert "Error" in result


# ── Provider inference ─────────────────────────────────────────────────────────

def test_log_task_infers_provider_local(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)
    codex_logger.log_task("qwen2.5-coder:3b", "Light", 50, "t")
    with open(temp_log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert "Local" in rows[0]["provider"]


def test_log_task_infers_provider_cloud(temp_log, monkeypatch):
    import codex_logger
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", temp_log)
    codex_logger.log_task("anthropic/claude-3.5-sonnet", "Orchestrator", 200, "t")
    with open(temp_log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert "Anthropic" in rows[0]["provider"]


# ── Thread safety ──────────────────────────────────────────────────────────────

def test_log_task_concurrent_writes_no_corruption(tmp_path, monkeypatch):
    import codex_logger
    log = str(tmp_path / "concurrent.csv")
    monkeypatch.setattr(codex_logger.config, "LOG_FILE", log)

    # Create header first
    import csv as _csv
    with open(log, "w", newline="") as f:
        _csv.writer(f).writerow(codex_logger.HEADER)

    errors = []

    def worker(i):
        try:
            result = codex_logger.log_task(
                f"model-{i}", "Heavy", i * 10, f"task-{i}"
            )
            if "Error" in result:
                errors.append(result)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == [], f"Thread errors: {errors}"

    with open(log, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 20, f"Expected 20 rows, got {len(rows)}"
