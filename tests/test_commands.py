"""
Tests for commands.py — dispatch table, flag guards (dry_run / no_cloud),
_flush_session, cmd_unlock, cmd_save, and _run_and_track.
HTTP calls are mocked so no real API traffic is produced.
"""
import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import config

from session_context import SessionContext
from session_stats import SessionStats
from commands import (
    dispatch,
    _flush_session,
    cmd_unlock,
    cmd_save,
    cmd_cache,
    cmd_stats,
    cmd_help,
    _run_and_track,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_ctx(tmp_path, monkeypatch, *, dry_run=False, no_cloud=False) -> SessionContext:
    """Create a minimal SessionContext with a mock orchestrator."""
    monkeypatch.setattr(config, "LOG_FILE",     str(tmp_path / "log.csv"))
    monkeypatch.setattr(config, "SESSION_FILE", str(tmp_path / "session.json"))
    orch = MagicMock()
    orch.router.route_request.return_value = ("coding", "QWEN32B", "scores(local=0, heavy=2, claude=0)")
    session = SessionStats(session_id="test", start_time=datetime.now().isoformat())
    return SessionContext(
        session=session, orchestrator=orch,
        dry_run=dry_run, no_cloud=no_cloud,
    )


# ── dispatch — exit / quit ─────────────────────────────────────────────────────

def test_dispatch_exit_returns_false(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    assert dispatch("exit", ctx) is False


def test_dispatch_quit_returns_false(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    assert dispatch("quit", ctx) is False


def test_dispatch_help_returns_true(tmp_path, monkeypatch, capsys):
    ctx = _make_ctx(tmp_path, monkeypatch)
    result = dispatch("help", ctx)
    assert result is True


def test_dispatch_unknown_command_returns_true(tmp_path, monkeypatch):
    """An unknown command is treated as a free-form task — must return True."""
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.orchestrator.execute_task.return_value = (None, "blocked")
    result = dispatch("completely unknown command xyz", ctx)
    assert result is True


def test_dispatch_status_calls_health(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    with patch("commands.cmd_status") as mock_status:
        dispatch("status", ctx)
        mock_status.assert_called_once()


def test_dispatch_cache_clear(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    with patch("commands.cmd_cache_clear") as mock_cc:
        dispatch("cache clear", ctx)
        mock_cc.assert_called_once()


# ── cmd_unlock ─────────────────────────────────────────────────────────────────

def test_cmd_unlock_removes_hard_lock(tmp_path, monkeypatch, no_locks):
    ctx = _make_ctx(tmp_path, monkeypatch)
    open(config.LOCK_FILE, "w").close()
    cmd_unlock(ctx, "")
    assert not os.path.exists(config.LOCK_FILE)


def test_cmd_unlock_removes_soft_lock(tmp_path, monkeypatch, no_locks):
    ctx = _make_ctx(tmp_path, monkeypatch)
    open(config.SOFT_LOCK_FILE, "w").close()
    cmd_unlock(ctx, "")
    assert not os.path.exists(config.SOFT_LOCK_FILE)


def test_cmd_unlock_no_locks_no_error(tmp_path, monkeypatch, no_locks):
    ctx = _make_ctx(tmp_path, monkeypatch)
    cmd_unlock(ctx, "")   # must not raise


# ── cmd_save ───────────────────────────────────────────────────────────────────

def test_cmd_save_no_last_content(tmp_path, monkeypatch, capsys):
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.last_content = None
    cmd_save(ctx, "output.py")
    captured = capsys.readouterr()
    assert "Nothing to save" in captured.out


def test_cmd_save_no_path(tmp_path, monkeypatch, capsys):
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.last_content = "x = 1"
    cmd_save(ctx, "")
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_save_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.last_content = "print('hello')"
    target = str(tmp_path / "out.py")
    cmd_save(ctx, target)
    assert os.path.exists(target)
    assert open(target).read() == "print('hello')"


# ── _flush_session ─────────────────────────────────────────────────────────────

def test_flush_session_creates_json(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    _flush_session(ctx)
    sf = config.SESSION_FILE
    assert os.path.exists(sf)
    data = json.loads(open(sf).read())
    assert "session_id" in data
    assert "tokens" in data
    assert "cost" in data
    assert "tasks" in data


def test_flush_session_updates_on_second_call(tmp_path, monkeypatch):
    from session_stats import TaskStats
    ctx = _make_ctx(tmp_path, monkeypatch)
    _flush_session(ctx)
    # Add a task
    ctx.session.tasks.append(TaskStats(
        task_id=1, task_summary="t", model="m", tier="Heavy",
        provider="Cloud", tokens=100, cost_usd=0.01,
        timestamp=datetime.now().isoformat(), status="Success",
    ))
    _flush_session(ctx)
    data = json.loads(open(config.SESSION_FILE).read())
    assert data["tasks"] == 1
    assert data["tokens"] == 100


# ── _run_and_track — dry_run flag ──────────────────────────────────────────────

def test_run_and_track_dry_run_skips_api(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch, dry_run=True)
    _run_and_track(ctx, "Refactor the module")
    ctx.orchestrator.execute_task.assert_not_called()


def test_run_and_track_dry_run_prints_routing(tmp_path, monkeypatch, capsys):
    ctx = _make_ctx(tmp_path, monkeypatch, dry_run=True)
    _run_and_track(ctx, "Format the code")
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out


def test_run_and_track_dry_run_no_session_task_added(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch, dry_run=True)
    _run_and_track(ctx, "some task")
    assert ctx.session.task_count == 0


# ── _run_and_track — no_cloud flag ────────────────────────────────────────────

def test_run_and_track_no_cloud_calls_try_local(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch, no_cloud=True)
    ctx.orchestrator._try_local.return_value = None   # local offline
    _run_and_track(ctx, "Format the code")
    ctx.orchestrator._try_local.assert_called_once()
    ctx.orchestrator.execute_task.assert_not_called()


def test_run_and_track_no_cloud_success(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch, no_cloud=True)
    fake_result = {
        "choices": [{"message": {"content": "done"}}],
        "model": "qwen2.5-coder:3b", "tier": "Light",
        "provider": "Local (Ollama)", "tokens": 50,
    }
    ctx.orchestrator._try_local.return_value = (fake_result, "ok")
    _run_and_track(ctx, "Format the code")
    assert ctx.last_content == "done"
    assert ctx.session.task_count == 1


def test_run_and_track_no_cloud_skip_logged(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch, no_cloud=True)
    ctx.orchestrator._try_local.return_value = None
    _run_and_track(ctx, "some task")
    assert ctx.session.task_count == 1
    assert ctx.session.tasks[0].status == "Skipped (no-cloud)"


# ── _run_and_track — normal path ──────────────────────────────────────────────

def test_run_and_track_normal_success(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    fake_result = {
        "choices": [{"message": {"content": "output"}}],
        "model": "qwen/qwen-2.5-coder-32b-instruct",
        "tier": "Heavy", "provider": "Cloud (Qwen Paid)", "tokens": 200,
    }
    ctx.orchestrator.execute_task.return_value = (fake_result, "✅ done")
    _run_and_track(ctx, "Refactor auth module")
    assert ctx.last_content == "output"
    assert ctx.session.task_count == 1
    assert ctx.session.tasks[0].status == "Success"


def test_run_and_track_api_failure(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.orchestrator.execute_task.return_value = (None, "CRITICAL FAILURE")
    _run_and_track(ctx, "some task")
    assert ctx.session.task_count == 1
    assert ctx.session.tasks[0].status == "Failed"


def test_run_and_track_increments_counter(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.orchestrator.execute_task.return_value = (None, "err")
    _run_and_track(ctx, "task 1")
    _run_and_track(ctx, "task 2")
    assert ctx.task_counter == 2


# ── cmd_plan — dry_run / no_cloud guards ──────────────────────────────────────

def test_cmd_plan_dry_run_prints_message(tmp_path, monkeypatch, capsys):
    from commands import cmd_plan
    ctx = _make_ctx(tmp_path, monkeypatch, dry_run=True)
    cmd_plan(ctx, "Build a REST API")
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out


def test_cmd_plan_dry_run_no_api_call(tmp_path, monkeypatch):
    from commands import cmd_plan
    ctx = _make_ctx(tmp_path, monkeypatch, dry_run=True)
    # WorkflowEngine is imported lazily inside cmd_plan; patch at its source
    with patch("task_executor.WorkflowEngine") as mock_engine:
        cmd_plan(ctx, "Build a REST API")
        mock_engine.assert_not_called()


def test_cmd_plan_no_cloud_blocked(tmp_path, monkeypatch, capsys):
    from commands import cmd_plan
    ctx = _make_ctx(tmp_path, monkeypatch, no_cloud=True)
    with patch("task_executor.WorkflowEngine") as mock_engine:
        cmd_plan(ctx, "Build a REST API")
        mock_engine.assert_not_called()
    captured = capsys.readouterr()
    assert "no-cloud" in captured.out.lower() or "blocked" in captured.out.lower() or "--no-cloud" in captured.out
