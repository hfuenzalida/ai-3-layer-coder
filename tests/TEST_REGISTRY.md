# Codex Futura — Test Registry

> **Last updated:** 2026-04-25  
> **Suite total:** 187 passing, 1 skipped  
> Run with: `python -m pytest tests/ -q`

---

## Coverage map

| File | Tests | What is covered |
|---|---|---|
| `test_cache.py` | 20 | get/put, TTL expiry, wrong model/prompt miss, replace on re-put, `stats()`, `clear_expired`, `clear_all`, hit/miss counters, `hit_rate`, concurrent thread-safety |
| `test_session_stats.py` | 22 | `infer_provider` for all vendor variants (local, Anthropic, OpenAI, DeepSeek, Qwen paid/free, unknown), `calculate_cost` exact/partial/fallback/free/zero/proportional, `SessionStats` empty/totals/by_model/by_tier/by_provider |
| `test_router.py` | 20 | Local/Heavy/Orchestrator keyword routing, no-keyword default, empty prompt, long-prompt penalty (>200 words), moderate-prompt score reduction (>150 words), word-boundary guards (`import` ≠ `important`, `comment` ≠ `commentary`), 3-tuple return shape, target model name, reason mentions scores |
| `test_task_executor.py` | 22 | `save_file` valid path, subdirectory creation, correct content, markdown fence stripping, path traversal block, absolute path outside OUTPUT_DIR block, empty path no-op, symlink attack block; `validate_python` clean/syntax-error/empty/fenced/fenced-with-error/non-python/no-leaked-temp-files; `_strip_markdown_fence` with/without language tag, preamble, trailing text, no fence, multiline, empty block, lone opening fence, JSON tag |
| `test_health.py` | 18 | `check_budget_lock` none/hard/soft/both (hard takes priority); `check_gitignore` missing/.env-protected/wildcard-*.env/.env.*/without-env/comment-lines-ignored; `check_reset_overdue` no-files/recent/old-stamp/boundary-35d/36d/log-ctime-fallback |
| `test_codex_logger.py` | 15 | Valid inputs write row, Light tier costs zero, `cost_override` valid/zero/negative/NaN/+inf/−inf/above-cap/at-cap/just-above-cap, provider inference local/cloud, concurrent 20-thread write safety |
| `test_orchestrator.py` | 30 | `_budget_gate` no-locks/hard-lock/soft-lock/hard+soft; circuit breaker starts-closed/opens-at-threshold/stays-closed-below-threshold/reports-open/resets-on-success/cooldown-expires/stays-open-during-cooldown; `_build_chain` coding→Heavy/strategic→Orchestrator/fallback-chain-depth; `_pre_flight_check` below-threshold/context-added-to-estimate/always-returns-bool; `_check_cache` miss/hit/hit-has-expected-keys; `execute_task` hard-lock/local-success/local-fails-escalates/cloud-success/user-cancel/returns-2-tuple/cache-hit-skips-http |
| `test_codex_detective.py` | 40 | `Finding` to_dict keys/values/no-file; `SecurityAuditor` hardcoded-secret/eval/shell=True/clean-file/gitignore-missing/gitignore-with-env/gitignore-without-env/.env-present/.env-absent; `CodeQualityInspector` long-function/TODO/FIXME/bare-except/duplicate-function/clean-code/missing-docstring/private-no-flag; `BugHunter` mutable-list-default/mutable-dict-default/immutable-not-flagged/assert-guard/float-equality/clean-code; `OperationalMonitor` hard-lock/soft-lock/no-locks/circuit-open/circuit-healthy/no-circuit-state/large-log/small-log; `run_scan` writes-jsonl/output-is-valid-jsonl; `load_last_scan` returns-latest-day/empty-log/no-log; severity ordering |

---

## Fixtures (conftest.py)

| Fixture | Scope | Purpose |
|---|---|---|
| `clean_cache` | function | Fresh SQLite DB per test + reset hit/miss counters + clear thread-local conn |
| `reset_circuit` | function | Reset module-level circuit-breaker globals before/after each test |
| `no_locks` | function | Guarantee hard + soft lock files do NOT exist during the test |
| `temp_log` | function | Fresh temp CSV path patched into `config.LOG_FILE` |

All config paths (`BASE_DIR`, `LOG_FILE`, `LOCK_FILE`, `SOFT_LOCK_FILE`, `MONITOR_CURRENT`, `SESSION_FILE`, `LAST_RESET_FILE`, `DETECTIVE_LOG`, `OUTPUT_DIR`, `TEMPLATES_DIR`) are redirected to a session-scoped `tempfile.mkdtemp()` so no test ever touches `~/codex_futura/`.

---

## Bugs caught during test authoring

| Test | Root cause | Fix applied |
|---|---|---|
| `test_route_local_keyword_docstring` | `\bdocstring\b` didn't match plural "docstrings" | Changed pattern to `\bdocstrings?\b` in `orchestrator.py` |
| `test_route_local_keyword_type_hint` | `\btype\s+hint\b` didn't match "type hints" | Changed pattern to `\btype\s+hints?\b` in `orchestrator.py` |
| `test_log_task_valid_inputs_writes_row` | Header only written at import time; patched path had no header | `log_task` now writes header inside the lock if file is new |

---

## Running subsets

```bash
# All tests
python -m pytest tests/ -q

# One module
python -m pytest tests/test_cache.py -v

# One keyword
python -m pytest tests/ -k "circuit" -v

# Show stdout (useful for detective verbose output)
python -m pytest tests/test_codex_detective.py -s
```
