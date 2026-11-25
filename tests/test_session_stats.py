"""
Tests for session_stats.py — provider inference, cost calculation, session aggregates.
Pure functions with no I/O; no fixtures needed beyond the session-level config patch.
"""
import pytest
from session_stats import (
    infer_provider,
    calculate_cost,
    SessionStats,
    TaskStats,
)
import config


# ── infer_provider ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected", [
    # Local — no forward-slash
    ("qwen2.5-coder:3b",               "Local (Ollama)"),
    ("llama3.1:8b",                     "Local (Ollama)"),
    ("codellama:13b",                   "Local (Ollama)"),
    # Anthropic / Claude
    ("anthropic/claude-3.5-sonnet",    "Cloud (Anthropic)"),
    ("anthropic/claude-3-haiku",       "Cloud (Anthropic)"),
    # OpenAI
    ("openai/gpt-4o",                  "Cloud (OpenAI)"),
    ("openai/gpt-3.5-turbo",           "Cloud (OpenAI)"),
    # DeepSeek
    ("deepseek/deepseek-chat-v3-0324", "Cloud (DeepSeek)"),
    # Qwen paid
    ("qwen/qwen-2.5-coder-32b-instruct", "Cloud (Qwen Paid)"),
    ("qwen/qwen3-coder",               "Cloud (Qwen Paid)"),
    # Qwen free
    ("qwen/qwen3-coder:free",          "Cloud (Qwen Free)"),
    ("qwen/qwen-2.5-coder-32b-instruct:free", "Cloud (Qwen Free)"),
    # Unknown cloud (has slash but no recognized vendor)
    ("mystery/model-v1",               "Cloud (Unknown)"),
])
def test_infer_provider(model, expected):
    assert infer_provider(model) == expected


def test_infer_provider_local_no_slash_important_word():
    """'important' contains 'import' — must not be confused with import keyword scoring."""
    # This tests that 'important' local model name still resolves as Local (Ollama)
    # because there is no '/' — the local check is structural, not keyword-based.
    assert infer_provider("important-model:latest") == "Local (Ollama)"


# ── calculate_cost ─────────────────────────────────────────────────────────────

def test_calculate_cost_exact_match_free():
    cost = calculate_cost("qwen/qwen-2.5-coder-32b-instruct:free", 1_000_000)
    assert cost == 0.0


def test_calculate_cost_exact_match_paid():
    # $0.07 per 1M tokens
    cost = calculate_cost("qwen/qwen-2.5-coder-32b-instruct", 1_000_000)
    assert abs(cost - 0.07) < 1e-9


def test_calculate_cost_claude():
    # $15 per 1M tokens
    cost = calculate_cost("anthropic/claude-3.5-sonnet", 1_000_000)
    assert abs(cost - 15.0) < 1e-9


def test_calculate_cost_partial_match():
    # "claude-3.5-sonnet" partial-matches "anthropic/claude-3.5-sonnet" key
    cost = calculate_cost("anthropic/claude-3.5-sonnet-20241022", 1_000_000)
    assert cost > 0.0  # should not fall back to 0


def test_calculate_cost_unknown_model_uses_fallback():
    cost = calculate_cost("unknown/mystery-model", 1_000_000)
    assert abs(cost - config.FALLBACK_COST_PER_1M) < 1e-9


def test_calculate_cost_zero_tokens():
    assert calculate_cost("anthropic/claude-3.5-sonnet", 0) == 0.0


def test_calculate_cost_proportional():
    full  = calculate_cost("anthropic/claude-3.5-sonnet", 1_000_000)
    half  = calculate_cost("anthropic/claude-3.5-sonnet",   500_000)
    assert abs(full / 2 - half) < 1e-9


# ── SessionStats aggregates ────────────────────────────────────────────────────

def _task(task_id=1, model="m", tier="Heavy", tokens=100, cost=0.01, provider="Cloud"):
    return TaskStats(
        task_id=task_id, task_summary="test",
        model=model, tier=tier, provider=provider,
        tokens=tokens, cost_usd=cost,
        timestamp="2026-01-01T00:00:00", status="Success",
    )


def test_session_stats_empty():
    s = SessionStats(session_id="x", start_time="2026-01-01")
    assert s.total_tokens == 0
    assert s.total_cost   == 0.0
    assert s.task_count   == 0


def test_session_stats_totals():
    s = SessionStats(session_id="x", start_time="2026-01-01")
    s.tasks = [_task(tokens=100, cost=0.01), _task(tokens=200, cost=0.02)]
    assert s.total_tokens == 300
    assert abs(s.total_cost - 0.03) < 1e-9
    assert s.task_count == 2


def test_session_stats_by_model():
    s = SessionStats(session_id="x", start_time="2026-01-01")
    s.tasks = [
        _task(model="gpt-4o",   tokens=100, cost=0.01),
        _task(model="gpt-4o",   tokens=200, cost=0.02),
        _task(model="claude",   tokens=300, cost=0.10),
    ]
    by = s.by_model
    assert by["gpt-4o"]["tokens"] == 300
    assert by["gpt-4o"]["count"]  == 2
    assert by["claude"]["tokens"] == 300
    assert by["claude"]["count"]  == 1


def test_session_stats_by_tier():
    s = SessionStats(session_id="x", start_time="2026-01-01")
    s.tasks = [
        _task(tier="Light",  tokens=50,  cost=0.0),
        _task(tier="Heavy",  tokens=200, cost=0.02),
        _task(tier="Heavy",  tokens=300, cost=0.03),
    ]
    by = s.by_tier
    assert by["Light"]["count"]  == 1
    assert by["Heavy"]["count"]  == 2
    assert by["Heavy"]["tokens"] == 500


def test_session_stats_by_provider_uses_infer_provider():
    """When task.provider is empty, by_provider falls back to infer_provider(model)."""
    s = SessionStats(session_id="x", start_time="2026-01-01")
    s.tasks = [
        TaskStats(task_id=1, task_summary="t", model="qwen2.5-coder:3b",
                  tier="Light", provider="",   tokens=50, cost_usd=0.0,
                  timestamp="2026-01-01", status="Success"),
        TaskStats(task_id=2, task_summary="t", model="anthropic/claude-3.5-sonnet",
                  tier="Orchestrator", provider="", tokens=100, cost_usd=0.10,
                  timestamp="2026-01-01", status="Success"),
    ]
    by = s.by_provider
    assert "Local (Ollama)"     in by
    assert "Cloud (Anthropic)"  in by
