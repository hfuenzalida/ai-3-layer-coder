"""
Tests for codex_detective.py — SecurityAuditor, CodeQualityInspector,
BugHunter, OperationalMonitor, and the JSONL log I/O helpers.
All file writes use tmp_path; no real ~/codex_futura/ paths are touched.
"""
import json
import os
import textwrap
from pathlib import Path

import pytest
import config

import codex_detective as det
from codex_detective import (
    Finding,
    SecurityAuditor,
    CodeQualityInspector,
    BugHunter,
    OperationalMonitor,
    run_scan,
    load_last_scan,
    SEVERITY_ORDER,
)


# ── Finding dataclass ──────────────────────────────────────────────────────────

def test_finding_to_dict_has_all_keys():
    f = Finding("TestInspector", "high", "TEST_RULE", "message", "foo.py", 42)
    d = f.to_dict()
    for key in ("ts", "inspector", "severity", "rule", "message", "file", "line"):
        assert key in d


def test_finding_to_dict_values():
    f = Finding("Sec", "critical", "HARDCODED_SECRET", "found key", "bar.py", 7)
    d = f.to_dict()
    assert d["inspector"] == "Sec"
    assert d["severity"]  == "critical"
    assert d["rule"]      == "HARDCODED_SECRET"
    assert d["file"]      == "bar.py"
    assert d["line"]      == 7


def test_finding_no_file():
    f = Finding("Sec", "info", "GITIGNORE_OK", "all good")
    d = f.to_dict()
    assert d["file"] == ""
    assert d["line"] is None


# ── SecurityAuditor ────────────────────────────────────────────────────────────

@pytest.fixture
def sec():
    return SecurityAuditor()


def test_security_hardcoded_secret_detected(tmp_path, monkeypatch):
    src = tmp_path / "secrets.py"
    src.write_text('API_KEY = "sk-abcdef1234567890xyz"\n', encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor().run()
    rules = [f.rule for f in findings]
    assert "HARDCODED_SECRET" in rules


def test_security_eval_detected(tmp_path, monkeypatch):
    src = tmp_path / "evil.py"
    src.write_text('result = eval(user_input)\n', encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor().run()
    assert any(f.rule == "UNSAFE_EVAL" for f in findings)


def test_security_shell_true_detected(tmp_path, monkeypatch):
    src = tmp_path / "runner.py"
    src.write_text('subprocess.run(cmd, shell=True)\n', encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor().run()
    assert any(f.rule == "SHELL_INJECTION" for f in findings)


def test_security_clean_file_no_secret_findings(tmp_path, monkeypatch):
    src = tmp_path / "clean.py"
    src.write_text('x = 1\nprint(x)\n', encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor().run()
    secret_findings = [f for f in findings if f.rule == "HARDCODED_SECRET"]
    assert secret_findings == []


def test_security_gitignore_missing_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor()._check_gitignore()
    assert any(f.rule == "MISSING_GITIGNORE" for f in findings)


def test_security_gitignore_with_env_ok(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n*.pyc\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor()._check_gitignore()
    assert any(f.rule == "GITIGNORE_OK" for f in findings)
    assert not any(f.rule == "MISSING_GITIGNORE" for f in findings)


def test_security_gitignore_without_env_flagged(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor()._check_gitignore()
    assert any(f.rule == "ENV_NOT_GITIGNORED" for f in findings)


def test_security_env_file_present_flagged(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET=123\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor()._check_env_file_not_committed()
    assert any(f.rule == "ENV_FILE_PRESENT" for f in findings)


def test_security_no_env_file_no_finding(tmp_path, monkeypatch):
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = SecurityAuditor()._check_env_file_not_committed()
    assert findings == []


# ── CodeQualityInspector ───────────────────────────────────────────────────────

@pytest.fixture
def cq():
    return CodeQualityInspector()


def test_quality_long_function_detected(tmp_path, monkeypatch):
    lines = ["def long_func():\n"] + [f"    x_{i} = {i}\n" for i in range(65)]
    src = tmp_path / "big.py"
    src.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "LONG_FUNCTION" for f in findings)


def test_quality_todo_detected(tmp_path, monkeypatch):
    src = tmp_path / "work.py"
    src.write_text("x = 1  # TODO: fix this later\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "TODO_IN_CODE" for f in findings)


def test_quality_fixme_detected(tmp_path, monkeypatch):
    src = tmp_path / "broken.py"
    src.write_text("# FIXME: this is wrong\ny = 2\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "TODO_IN_CODE" for f in findings)


def test_quality_bare_except_detected(tmp_path, monkeypatch):
    src = tmp_path / "swallow.py"
    src.write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "BARE_EXCEPT" for f in findings)


def test_quality_duplicate_function_detected(tmp_path, monkeypatch):
    src = tmp_path / "dup.py"
    src.write_text(
        "def my_func():\n    pass\n\ndef my_func():\n    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "DUPLICATE_FUNCTION" for f in findings)


def test_quality_clean_code_no_structural_findings(tmp_path, monkeypatch):
    src = tmp_path / "good.py"
    src.write_text(
        'def add(a, b):\n    """Return sum."""\n    return a + b\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    structural = [f for f in findings if f.rule in (
        "LONG_FUNCTION", "BARE_EXCEPT", "DUPLICATE_FUNCTION",
    )]
    assert structural == []


def test_quality_missing_docstring_detected(tmp_path, monkeypatch):
    src = tmp_path / "nodoc.py"
    src.write_text("def public_func():\n    return 42\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert any(f.rule == "MISSING_DOCSTRING" and "public_func" in f.message
               for f in findings)


def test_quality_private_func_no_docstring_not_flagged(tmp_path, monkeypatch):
    src = tmp_path / "private.py"
    src.write_text("def _helper():\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = CodeQualityInspector().run()
    assert not any(f.rule == "MISSING_DOCSTRING" and "_helper" in f.message
                   for f in findings)


# ── BugHunter ──────────────────────────────────────────────────────────────────

def test_bug_mutable_default_detected(tmp_path, monkeypatch):
    src = tmp_path / "mutable.py"
    src.write_text("def f(items=[]):\n    return items\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert any(f.rule == "MUTABLE_DEFAULT" for f in findings)


def test_bug_mutable_dict_default_detected(tmp_path, monkeypatch):
    src = tmp_path / "mdict.py"
    src.write_text("def g(opts={}):\n    return opts\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert any(f.rule == "MUTABLE_DEFAULT" for f in findings)


def test_bug_immutable_default_not_flagged(tmp_path, monkeypatch):
    src = tmp_path / "ok.py"
    src.write_text("def h(x=None, y=0, z=''):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert not any(f.rule == "MUTABLE_DEFAULT" for f in findings)


def test_bug_assert_guard_detected(tmp_path, monkeypatch):
    src = tmp_path / "guard.py"
    src.write_text("def validate(x):\n    assert x > 0\n    return x\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert any(f.rule == "ASSERT_GUARD" for f in findings)


def test_bug_float_equality_detected(tmp_path, monkeypatch):
    src = tmp_path / "floatcmp.py"
    src.write_text("if total == 0.0:\n    print('zero')\n", encoding="utf-8")
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert any(f.rule == "FLOAT_EQUALITY" for f in findings)


def test_bug_clean_code_no_findings(tmp_path, monkeypatch):
    src = tmp_path / "clean.py"
    src.write_text(
        "def add(a, b=None):\n    if b is None:\n        b = 0\n    return a + b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    findings = BugHunter().run()
    assert not any(f.rule in ("MUTABLE_DEFAULT", "FLOAT_EQUALITY") for f in findings)


# ── OperationalMonitor ─────────────────────────────────────────────────────────

def test_ops_hard_lock_detected(no_locks, monkeypatch):
    open(config.LOCK_FILE, "w").close()
    findings = OperationalMonitor()._check_lock_files()
    assert any(f.rule == "HARD_LOCK_ACTIVE" for f in findings)
    assert any(f.severity == "critical" for f in findings)


def test_ops_soft_lock_detected(no_locks, monkeypatch):
    open(config.SOFT_LOCK_FILE, "w").close()
    findings = OperationalMonitor()._check_lock_files()
    assert any(f.rule == "SOFT_LOCK_ACTIVE" for f in findings)


def test_ops_no_locks_clear(no_locks):
    findings = OperationalMonitor()._check_lock_files()
    assert any(f.rule == "BUDGET_LOCK_CLEAR" for f in findings)
    assert any(f.severity == "info" for f in findings)


def test_ops_circuit_open_detected(tmp_path, monkeypatch):
    import time
    cb = tmp_path / "circuit_breaker.json"
    cb.write_text(json.dumps({
        "failure_count": 3,
        "circuit_open": True,
        "circuit_open_until": time.time() + 9999,
    }), encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    findings = OperationalMonitor()._check_circuit_breaker()
    assert any(f.rule == "CIRCUIT_OPEN" for f in findings)
    assert any(f.severity == "high" for f in findings)


def test_ops_circuit_healthy(tmp_path, monkeypatch):
    cb = tmp_path / "circuit_breaker.json"
    cb.write_text(json.dumps({"failure_count": 0, "circuit_open": False,
                               "circuit_open_until": 0.0}), encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    findings = OperationalMonitor()._check_circuit_breaker()
    assert any(f.rule == "CIRCUIT_HEALTHY" for f in findings)


def test_ops_no_circuit_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    findings = OperationalMonitor()._check_circuit_breaker()
    assert any(f.rule == "CIRCUIT_NO_STATE" for f in findings)


def test_ops_large_detective_log_flagged(tmp_path, monkeypatch):
    log = tmp_path / "detective_log.jsonl"
    log.write_bytes(b"x" * (6 * 1024 * 1024))  # 6 MB
    monkeypatch.setattr(config, "DETECTIVE_LOG", str(log))
    findings = OperationalMonitor()._check_detective_log_size()
    assert any(f.rule == "DETECTIVE_LOG_LARGE" for f in findings)


def test_ops_small_detective_log_not_flagged(tmp_path, monkeypatch):
    log = tmp_path / "detective_log.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config, "DETECTIVE_LOG", str(log))
    findings = OperationalMonitor()._check_detective_log_size()
    assert not any(f.rule == "DETECTIVE_LOG_LARGE" for f in findings)


# ── run_scan / load_last_scan ──────────────────────────────────────────────────

def test_run_scan_writes_jsonl(tmp_path, monkeypatch):
    """run_scan must create / append to the JSONL log."""
    log = str(tmp_path / "detective_log.jsonl")
    monkeypatch.setattr(config, "DETECTIVE_LOG", log)
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    # Point PROJECT_ROOT at tmp_path so no real source is scanned
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")

    findings = run_scan(verbose=False)
    assert os.path.exists(log)
    assert len(findings) > 0
    with open(log, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) == len(findings)


def test_run_scan_output_is_valid_jsonl(tmp_path, monkeypatch):
    log = str(tmp_path / "det.jsonl")
    monkeypatch.setattr(config, "DETECTIVE_LOG", log)
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(det, "PROJECT_ROOT", tmp_path)

    run_scan(verbose=False)
    with open(log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)   # must not raise
                assert "severity" in d
                assert "rule" in d


def test_load_last_scan_returns_latest_day(tmp_path, monkeypatch):
    log = str(tmp_path / "det.jsonl")
    monkeypatch.setattr(config, "DETECTIVE_LOG", log)
    # Write two "days" worth of findings
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-01-01T10:00:00+00:00", "inspector": "A",
                             "severity": "low", "rule": "OLD", "message": "old",
                             "file": "", "line": None}) + "\n")
        f.write(json.dumps({"ts": "2026-01-02T10:00:00+00:00", "inspector": "B",
                             "severity": "high", "rule": "NEW", "message": "new",
                             "file": "", "line": None}) + "\n")

    findings = load_last_scan()
    assert len(findings) == 1
    assert findings[0].rule == "NEW"


def test_load_last_scan_empty_log_returns_empty(tmp_path, monkeypatch):
    log = str(tmp_path / "empty.jsonl")
    open(log, "w").close()
    monkeypatch.setattr(config, "DETECTIVE_LOG", log)
    assert load_last_scan() == []


def test_load_last_scan_no_log_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DETECTIVE_LOG", str(tmp_path / "no_file.jsonl"))
    assert load_last_scan() == []


# ── Severity ordering ──────────────────────────────────────────────────────────

def test_severity_order_critical_lowest_number():
    assert SEVERITY_ORDER["critical"] < SEVERITY_ORDER["high"]
    assert SEVERITY_ORDER["high"]     < SEVERITY_ORDER["medium"]
    assert SEVERITY_ORDER["medium"]   < SEVERITY_ORDER["low"]
    assert SEVERITY_ORDER["low"]      < SEVERITY_ORDER["info"]
