"""
Tests for cache.py — SQLite response cache.
Every test uses the `clean_cache` fixture for full isolation.
"""
import time
import threading
import pytest


# ── Basic get/put ──────────────────────────────────────────────────────────────

def test_get_miss_returns_none(clean_cache):
    assert clean_cache.get("prompt", "model", "Heavy") is None


def test_put_then_get_returns_value(clean_cache):
    clean_cache.put("hello prompt", "gpt-4o", "Heavy", "response text", 42)
    result = clean_cache.get("hello prompt", "gpt-4o", "Heavy")
    assert result is not None
    assert result["content"] == "response text"
    assert result["tokens"]  == 42
    assert result["model"]   == "gpt-4o"


def test_get_wrong_model_misses(clean_cache):
    clean_cache.put("p", "model-a", "Heavy", "content", 10)
    assert clean_cache.get("p", "model-b", "Heavy") is None


def test_get_wrong_prompt_misses(clean_cache):
    clean_cache.put("prompt-a", "m", "Heavy", "content", 10)
    assert clean_cache.get("prompt-b", "m", "Heavy") is None


def test_put_replace_updates_content(clean_cache):
    clean_cache.put("p", "m", "Heavy", "v1", 10)
    clean_cache.put("p", "m", "Heavy", "v2", 20)
    result = clean_cache.get("p", "m", "Heavy")
    assert result["content"] == "v2"
    assert result["tokens"]  == 20


# ── TTL expiry ─────────────────────────────────────────────────────────────────

def test_get_expired_entry_returns_none(clean_cache):
    """Manually insert a row with an old timestamp so it reads as expired."""
    conn = clean_cache._get_conn()
    key  = clean_cache._key("p", "m")
    # Write with created_at far in the past
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, tier, model, content, tokens, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (key, "Heavy", "m", "old", 1, time.time() - 999_999),
    )
    conn.commit()
    assert clean_cache.get("p", "m", "Heavy") is None


def test_get_fresh_entry_returns_value(clean_cache):
    clean_cache.put("p", "m", "Light", "fresh", 5)
    assert clean_cache.get("p", "m", "Light") is not None


# ── stats ──────────────────────────────────────────────────────────────────────

def test_stats_empty_db(clean_cache):
    s = clean_cache.stats()
    assert s["total"]    == 0
    assert s["by_tier"]  == []
    assert s["hit_rate"] is None


def test_stats_reflects_entries(clean_cache):
    clean_cache.put("p1", "m", "Heavy", "c1", 100)
    clean_cache.put("p2", "m", "Heavy", "c2", 200)
    clean_cache.put("p3", "m", "Light", "c3",  50)
    s = clean_cache.stats()
    assert s["total"] == 3
    tiers = {row[0]: row for row in s["by_tier"]}
    assert tiers["Heavy"][1] == 2   # 2 entries
    assert tiers["Heavy"][2] == 300 # 300 tokens
    assert tiers["Light"][1] == 1


# ── clear_expired / clear_all ──────────────────────────────────────────────────

def test_clear_expired_removes_stale_keeps_fresh(clean_cache):
    clean_cache.put("fresh", "m", "Light", "ok", 1)
    # Manually insert a stale row
    conn = clean_cache._get_conn()
    key  = clean_cache._key("stale", "m")
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, tier, model, content, tokens, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (key, "Light", "m", "old", 1, time.time() - 999_999),
    )
    conn.commit()
    deleted = clean_cache.clear_expired()
    assert deleted == 1
    assert clean_cache.get("fresh", "m", "Light") is not None
    assert clean_cache.get("stale", "m", "Light") is None


def test_clear_all_removes_everything(clean_cache):
    for i in range(5):
        clean_cache.put(f"p{i}", "m", "Heavy", f"c{i}", i)
    n = clean_cache.clear_all()
    assert n == 5
    assert clean_cache.stats()["total"] == 0


def test_clear_all_returns_zero_on_empty(clean_cache):
    assert clean_cache.clear_all() == 0


# ── Hit / miss counters ────────────────────────────────────────────────────────

def test_miss_increments_misses(clean_cache):
    clean_cache.get("nope", "m", "Heavy")
    assert clean_cache._misses == 1
    assert clean_cache._hits   == 0


def test_hit_increments_hits(clean_cache):
    clean_cache.put("p", "m", "Heavy", "c", 1)
    clean_cache.get("p", "m", "Heavy")
    assert clean_cache._hits   == 1
    assert clean_cache._misses == 0


def test_hit_rate_in_stats(clean_cache):
    clean_cache.put("p", "m", "Heavy", "c", 1)
    clean_cache.get("p", "m", "Heavy")   # hit
    clean_cache.get("x", "m", "Heavy")   # miss
    s = clean_cache.stats()
    assert s["hits"]     == 1
    assert s["misses"]   == 1
    assert abs(s["hit_rate"] - 0.5) < 1e-9


def test_hit_rate_none_before_any_lookup(clean_cache):
    assert clean_cache.stats()["hit_rate"] is None


# ── Thread safety (basic smoke test) ──────────────────────────────────────────

def test_concurrent_puts_do_not_corrupt(clean_cache):
    errors = []

    def worker(i):
        try:
            clean_cache.put(f"p{i}", "m", "Heavy", f"content-{i}", i)
            result = clean_cache.get(f"p{i}", "m", "Heavy")
            assert result is not None
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == [], f"Thread errors: {errors}"
    assert clean_cache.stats()["total"] == 20
