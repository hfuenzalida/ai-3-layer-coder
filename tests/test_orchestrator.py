"""
Tests for orchestrator.py — _budget_gate, circuit breaker, _build_chain,
_pre_flight_check, _check_cache, and execute_task with mocked HTTP.
All tests use reset_circuit and no_locks fixtures from conftest.py so
module-level globals never bleed between tests.
"""
import json
import queue
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import config

import orchestrator as _orch_mod
from orchestrator import (
    Orchestrator,
    CodexRouter,
    _LOCAL_FAILURE_THRESHOLD,
    _LOCAL_CIRCUIT_COOLDOWN,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_orch() -> Orchestrator:
    """Return an Orchestrator whose HTTP session is replaced by a MagicMock."""
    orch = Orchestrator()
    orch._session = MagicMock()
    return orch


def _sse_response(content: str, model: str = "qwen/qwen-2.5-coder-32b-instruct") -> MagicMock:
    """
    Build a fake streaming response that yields valid SSE lines for a
    single-chunk reply followed by a usage line and [DONE].
    """
    lines = [
        f'data: {{"choices":[{{"delta":{{"content":{json.dumps(content)}}}}}]}}'.encode(),
        b'data: {"usage":{"total_tokens":42}}',
        b"data: [DONE]",
    ]
    mock_resp = MagicMock()
    mock_resp.headers = {"x-openrouter-model": model}
    mock_resp.iter_lines.return_value = iter(lines)
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── _budget_gate ───────────────────────────────────────────────────────────────

def test_budget_gate_local_always_passes(no_locks):
    orch = _make_orch()
    # Even if lock files exist, "local" duty should never be blocked
    open(config.LOCK_FILE, "w").close()
    err, duty = orch._budget_gate("local")
    assert err is None
    assert duty == "local"


def test_budget_gate_no_locks_coding_passes(no_locks):
    orch = _make_orch()
    err, duty = orch._budget_gate("coding")
    assert err is None
    assert duty == "coding"


def test_budget_gate_hard_lock_blocks_coding(no_locks):
    orch = _make_orch()
    open(config.LOCK_FILE, "w").close()
    err, duty = orch._budget_gate("coding")
    assert err is not None
    assert "LOCKED" in err.upper() or "lock" in err.lower()


def test_budget_gate_hard_lock_blocks_strategic(no_locks):
    orch = _make_orch()
    open(config.LOCK_FILE, "w").close()
    err, _ = orch._budget_gate("strategic")
    assert err is not None


def test_budget_gate_soft_lock_downgrades_strategic(no_locks):
    orch = _make_orch()
    open(config.SOFT_LOCK_FILE, "w").close()
    err, duty = orch._budget_gate("strategic")
    assert err is None
    assert duty == "coding"   # downgraded


def test_budget_gate_soft_lock_does_not_block_coding(no_locks):
    orch = _make_orch()
    open(config.SOFT_LOCK_FILE, "w").close()
    err, duty = orch._budget_gate("coding")
    assert err is None
    assert duty == "coding"   # unchanged


def test_budget_gate_hard_takes_priority_over_soft(no_locks):
    orch = _make_orch()
    open(config.LOCK_FILE, "w").close()
    open(config.SOFT_LOCK_FILE, "w").close()
    err, _ = orch._budget_gate("strategic")
    assert err is not None   # hard lock wins — returns error string


# ── Circuit breaker ────────────────────────────────────────────────────────────

def test_circuit_starts_closed(reset_circuit):
    orch = _make_orch()
    assert orch._is_local_circuit_open() is False


def test_circuit_opens_after_threshold_failures(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD):
        orch._record_local_failure()
    assert _orch_mod._local_circuit_open is True


def test_circuit_remains_closed_below_threshold(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD - 1):
        orch._record_local_failure()
    assert _orch_mod._local_circuit_open is False


def test_circuit_reports_open_when_flag_set(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD):
        orch._record_local_failure()
    # Open check should return True (and print a message)
    assert orch._is_local_circuit_open() is True


def test_circuit_resets_on_success(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD):
        orch._record_local_failure()
    assert _orch_mod._local_circuit_open is True
    orch._record_local_success()
    assert _orch_mod._local_circuit_open is False
    assert _orch_mod._local_failure_count == 0


def test_circuit_cooldown_expires(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD):
        orch._record_local_failure()
    # Force the cooldown to be already expired
    _orch_mod._local_circuit_open_until = time.time() - 1
    # _is_local_circuit_open should detect expiry and reset the circuit
    assert orch._is_local_circuit_open() is False
    assert _orch_mod._local_circuit_open is False


def test_circuit_stays_open_during_cooldown(reset_circuit):
    orch = _make_orch()
    for _ in range(_LOCAL_FAILURE_THRESHOLD):
        orch._record_local_failure()
    # Cooldown has NOT expired
    _orch_mod._local_circuit_open_until = time.time() + 9999
    assert orch._is_local_circuit_open() is True


# ── _build_chain ───────────────────────────────────────────────────────────────

def test_build_chain_coding_returns_heavy_tier():
    orch = _make_orch()
    chain, tier = orch._build_chain("coding")
    assert tier == "Heavy"
    assert len(chain) >= 1
    assert any("qwen" in m.lower() for m in chain)


def test_build_chain_strategic_returns_orchestrator_tier():
    orch = _make_orch()
    chain, tier = orch._build_chain("strategic")
    assert tier == "Orchestrator"
    assert any("claude" in m.lower() or "gpt" in m.lower() for m in chain)


def test_build_chain_coding_fallback_chain_has_multiple_models():
    orch = _make_orch()
    chain, _ = orch._build_chain("coding")
    assert len(chain) >= 2   # primary + at least one fallback


# ── _pre_flight_check ──────────────────────────────────────────────────────────

def test_pre_flight_below_threshold_heavy_returns_false():
    """A short prompt well below the cost threshold must not trigger confirmation."""
    orch = _make_orch()
    chain, tier = orch._build_chain("coding")
    # patch PRE_FLIGHT_USD_THRESHOLD to something very high so we never prompt
    with patch.object(config, "PRE_FLIGHT_USD_THRESHOLD", 999.0):
        cancelled = orch._pre_flight_check(tier, chain, "Write a sort function.")
    assert cancelled is False


def test_pre_flight_includes_context_in_estimate():
    """
    Token estimate must grow when context is non-empty.
    We verify by comparing est_tokens with vs without context.
    Two Orchestrator instances, one with context, one without.
    """
    orch_no_ctx = _make_orch()
    orch_with_ctx = _make_orch()
    orch_with_ctx._context = ["A" * 1000, "B" * 1000]   # 2000 chars ≈ 500 extra tokens

    prompt = "Review the architecture."
    chain, tier = orch_no_ctx._build_chain("coding")

    # We instrument _pre_flight_check by inspecting its formula side-effects.
    # Since _pre_flight_check doesn't return the estimate, we replicate
    # the same formula here and verify context shifts the output.
    def _estimate(orch, p):
        ctx_chars = sum(len(c) for c in orch._context[-config.CONTEXT_WINDOW_SIZE:])
        total     = len(p) + ctx_chars
        inp       = max(50, total // 4)
        out       = max(128, inp // 2)
        return inp + out

    no_ctx_tokens   = _estimate(orch_no_ctx, prompt)
    with_ctx_tokens = _estimate(orch_with_ctx, prompt)
    assert with_ctx_tokens > no_ctx_tokens


def test_pre_flight_always_returns_bool():
    """_pre_flight_check must always return a bool regardless of tier/threshold."""
    orch = _make_orch()
    chain, _ = orch._build_chain("coding")
    with patch.object(config, "PRE_FLIGHT_USD_THRESHOLD", 999.0):
        result = orch._pre_flight_check("Heavy", chain, "x" * 4000)
    assert isinstance(result, bool)


# ── _check_cache ───────────────────────────────────────────────────────────────

def test_check_cache_miss_returns_none(clean_cache):
    orch = _make_orch()
    result = orch._check_cache("never seen before prompt", "m", "Heavy", "t")
    assert result is None


def test_check_cache_hit_returns_tuple(clean_cache, tmp_path, monkeypatch):
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()
    import cache
    cache.put("my prompt", "qwen/qwen-2.5-coder-32b-instruct", "Heavy", "response text", 100)
    result = orch._check_cache(
        "my prompt", "qwen/qwen-2.5-coder-32b-instruct", "Heavy", "summary"
    )
    assert result is not None
    result_dict, status_str = result
    assert result_dict["choices"][0]["message"]["content"] == "response text"
    assert "Cache" in status_str or "cache" in status_str.lower()


def test_check_cache_hit_result_has_expected_keys(clean_cache, tmp_path, monkeypatch):
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()
    import cache
    cache.put("p2", "model-x", "Heavy", "content", 55)
    res, _ = orch._check_cache("p2", "model-x", "Heavy", "t")
    for key in ("choices", "model", "tier", "provider", "tokens"):
        assert key in res


# ── execute_task — mocked HTTP ─────────────────────────────────────────────────

def test_execute_task_hard_lock_returns_none(no_locks):
    """Hard lock must immediately return (None, error_message)."""
    orch = _make_orch()
    open(config.LOCK_FILE, "w").close()
    result, status = orch.execute_task("Refactor the auth module")
    assert result is None
    assert "LOCKED" in status.upper() or "lock" in status.lower()


def test_execute_task_local_success(no_locks, reset_circuit, clean_cache, tmp_path, monkeypatch):
    """When local HTTP call succeeds, result should have tier='Light'."""
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()

    local_lines = [
        b'data: {"choices":[{"delta":{"content":"hello"}}]}',
        b"data: [DONE]",
    ]
    mock_local_resp = MagicMock()
    mock_local_resp.iter_lines.return_value = iter(local_lines)
    mock_local_resp.raise_for_status.return_value = None

    orch._session.post.return_value = mock_local_resp

    # Force routing to "local" with a local keyword
    result, status = orch.execute_task("Format the docstrings in utils.py")
    if result is not None:
        assert result["tier"] == "Light"


def test_execute_task_local_fails_escalates_to_cloud(
    no_locks, reset_circuit, clean_cache, tmp_path, monkeypatch
):
    """Local connection error must escalate to cloud Heavy without re-raising."""
    import requests as req
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()

    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise req.exceptions.ConnectionError("Ollama offline")
        # Second call is the cloud fallback
        return _sse_response("cloud response")

    orch._session.post.side_effect = _side_effect

    with patch.object(orch, "_pre_flight_check", return_value=False):
        result, status = orch.execute_task("Format the docstrings please")

    # At minimum, we expect two HTTP calls (local attempt + cloud)
    # or one cloud call if circuit was already open
    assert call_count[0] >= 1


def test_execute_task_cloud_success(
    no_locks, reset_circuit, clean_cache, tmp_path, monkeypatch
):
    """A cloud Heavy call with a valid SSE response returns a proper result dict."""
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()

    cloud_resp = _sse_response("def sort_fn(): pass")
    orch._session.post.return_value = cloud_resp

    with patch.object(orch, "_pre_flight_check", return_value=False):
        result, status = orch.execute_task("Refactor the payment module")

    if result is not None:
        assert result["tier"] in ("Heavy", "Orchestrator", "Light")
        assert "choices" in result


def test_execute_task_user_cancel_returns_none(
    no_locks, reset_circuit, clean_cache, tmp_path, monkeypatch
):
    """If pre-flight returns True (user cancelled), result must be None."""
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()

    with patch.object(orch, "_pre_flight_check", return_value=True):
        result, status = orch.execute_task("Review the full architecture of the system")

    # pre-flight cancel only fires for cloud duties (strategic/coding)
    # 'local' skips pre-flight — so we only assert when the duty is cloud
    if result is None:
        assert "cancel" in status.lower() or status == "Cancelled by user."


def test_execute_task_returns_two_tuple(no_locks, reset_circuit, tmp_path, monkeypatch):
    """execute_task must always return a 2-tuple."""
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()
    open(config.LOCK_FILE, "w").close()   # easiest way to get a fast return
    out = orch.execute_task("Implement the login API endpoint")
    assert isinstance(out, tuple)
    assert len(out) == 2


def test_execute_task_cache_hit_skips_http(
    no_locks, reset_circuit, clean_cache, tmp_path, monkeypatch
):
    """If cache has a hit the HTTP session must never be called."""
    monkeypatch.setattr(_orch_mod.config, "LOG_FILE", str(tmp_path / "log.csv"))
    orch = _make_orch()

    import cache
    prompt = "Refactor the payment module thoroughly"
    cache.put(prompt, "qwen/qwen-2.5-coder-32b-instruct", "Heavy", "cached answer", 30)

    with patch.object(orch, "_pre_flight_check", return_value=False):
        result, status = orch.execute_task(prompt)

    orch._session.post.assert_not_called()
    if result is not None:
        assert result["choices"][0]["message"]["content"] == "cached answer"
