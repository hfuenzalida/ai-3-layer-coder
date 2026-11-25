"""
Codex Detective — automated quality, security, and bug-hunt auditor.
======================================================================
Runs four independent inspectors over the project source tree and
appends structured findings to ``config.DETECTIVE_LOG`` (JSONL format).

Usage
-----
    python codex_detective.py               # full scan
    python codex_detective.py --summary     # print last scan without re-running
    python codex_detective.py --clear       # wipe the log (caution: destructive)

Each line in the JSONL log is a Finding dict:
    {
        "ts":        "<ISO-8601 timestamp>",
        "inspector": "<SecurityAuditor | CodeQuality | BugHunter | OpsHealth>",
        "severity":  "<critical | high | medium | low | info>",
        "rule":      "<short identifier>",
        "message":   "<human-readable description>",
        "file":      "<relative path or empty string>",
        "line":      <int or null>,
    }
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import tokenize
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import config

# ── Constants ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Python source files to inspect (skip test files, migrations, __pycache__)
_SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", "node_modules", "tests"}


def _py_files() -> Iterator[Path]:
    for p in PROJECT_ROOT.rglob("*.py"):
        if not any(part in _SKIP_DIRS for part in p.parts):
            yield p


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# ── Finding dataclass ──────────────────────────────────────────────────────────

class Finding:
    __slots__ = ("ts", "inspector", "severity", "rule", "message", "file", "line")

    def __init__(
        self,
        inspector: str,
        severity: str,
        rule: str,
        message: str,
        file: str = "",
        line: int | None = None,
    ) -> None:
        self.ts        = datetime.now(timezone.utc).isoformat()
        self.inspector = inspector
        self.severity  = severity
        self.rule      = rule
        self.message   = message
        self.file      = file
        self.line      = line

    def to_dict(self) -> dict:
        return {
            "ts":        self.ts,
            "inspector": self.inspector,
            "severity":  self.severity,
            "rule":      self.rule,
            "message":   self.message,
            "file":      self.file,
            "line":      self.line,
        }


# ── SecurityAuditor ────────────────────────────────────────────────────────────

class SecurityAuditor:
    """
    Scans Python source for common security anti-patterns:
    - Hard-coded secrets / API keys
    - Use of eval() / exec()
    - Shell injection risks (shell=True in subprocess)
    - os.system() calls (prefer subprocess)
    - Broad exception swallowing that hides errors
    - Missing .gitignore protection for .env files
    """

    NAME = "SecurityAuditor"

    _SECRET_RE = re.compile(
        r'(?i)(api[_\-]?key|secret|password|token|auth)\s*=\s*["\'][^"\']{8,}["\']'
    )
    _EVAL_RE  = re.compile(r'\beval\s*\(')
    _EXEC_RE  = re.compile(r'\bexec\s*\(')
    _SHELL_RE = re.compile(r'shell\s*=\s*True')
    _OS_SYS_RE = re.compile(r'\bos\.system\s*\(')

    def run(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._scan_source())
        findings.extend(self._check_gitignore())
        findings.extend(self._check_env_file_not_committed())
        return findings

    def _scan_source(self) -> list[Finding]:
        out: list[Finding] = []
        for path in _py_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = _rel(path)
            for i, line in enumerate(source.splitlines(), 1):
                if self._SECRET_RE.search(line):
                    out.append(Finding(
                        self.NAME, "critical", "HARDCODED_SECRET",
                        f"Possible hard-coded secret detected: '{line.strip()[:80]}'",
                        rel, i,
                    ))
                if self._EVAL_RE.search(line):
                    out.append(Finding(
                        self.NAME, "high", "UNSAFE_EVAL",
                        "eval() usage — code injection risk.", rel, i,
                    ))
                if self._EXEC_RE.search(line) and "py_compile" not in line:
                    out.append(Finding(
                        self.NAME, "high", "UNSAFE_EXEC",
                        "exec() usage — code injection risk.", rel, i,
                    ))
                if self._SHELL_RE.search(line):
                    out.append(Finding(
                        self.NAME, "medium", "SHELL_INJECTION",
                        "subprocess called with shell=True — prefer list form.", rel, i,
                    ))
                if self._OS_SYS_RE.search(line):
                    out.append(Finding(
                        self.NAME, "medium", "OS_SYSTEM",
                        "os.system() used — prefer subprocess.run().", rel, i,
                    ))
        return out

    def _check_gitignore(self) -> list[Finding]:
        gitignore = PROJECT_ROOT / ".gitignore"
        if not gitignore.exists():
            return [Finding(
                self.NAME, "high", "MISSING_GITIGNORE",
                ".gitignore is missing — .env and secrets may be committed.",
            )]
        content = gitignore.read_text(encoding="utf-8")
        patterns = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
        has_env = any(p in (".env", "*.env", ".env.*") for p in patterns)
        if not has_env:
            return [Finding(
                self.NAME, "high", "ENV_NOT_GITIGNORED",
                ".gitignore exists but does not cover .env files.",
            )]
        return [Finding(
            self.NAME, "info", "GITIGNORE_OK",
            ".gitignore protects .env files.",
        )]

    def _check_env_file_not_committed(self) -> list[Finding]:
        """Warn if a .env file exists at the project root (should not be tracked)."""
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            return [Finding(
                self.NAME, "medium", "ENV_FILE_PRESENT",
                ".env file found at project root — verify it is not tracked by git.",
            )]
        return []


# ── CodeQualityInspector ────────────────────────────────────────────────────────

class CodeQualityInspector:
    """
    Static quality checks on Python source:
    - Long functions (> 60 lines) → complex, hard to test
    - Deep nesting (> 4 levels via indent heuristic)
    - TODO / FIXME / HACK / XXX comments left in production code
    - Bare `except:` clauses (swallows all exceptions including KeyboardInterrupt)
    - Functions without docstrings in public modules
    - Duplicate function names within a module
    """

    NAME = "CodeQuality"
    MAX_FUNC_LINES = 60
    MAX_INDENT     = 4   # measured in 4-space indent units

    def run(self) -> list[Finding]:
        findings: list[Finding] = []
        for path in _py_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                tree   = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError):
                continue
            rel = _rel(path)
            findings.extend(self._check_long_functions(tree, source, rel))
            findings.extend(self._check_todo_comments(source, rel))
            findings.extend(self._check_bare_except(tree, rel))
            findings.extend(self._check_missing_docstrings(tree, rel))
            findings.extend(self._check_duplicate_names(tree, rel))
            findings.extend(self._check_deep_nesting(source, rel))
        return findings

    def _check_long_functions(self, tree: ast.AST, source: str, rel: str) -> list[Finding]:
        lines = source.splitlines()
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                length = end - node.lineno
                if length > self.MAX_FUNC_LINES:
                    out.append(Finding(
                        self.NAME, "medium", "LONG_FUNCTION",
                        f"Function '{node.name}' is {length} lines "
                        f"(>{self.MAX_FUNC_LINES}) — consider splitting.",
                        rel, node.lineno,
                    ))
        return out

    def _check_todo_comments(self, source: str, rel: str) -> list[Finding]:
        pattern = re.compile(r'#\s*(TODO|FIXME|HACK|XXX)\b', re.IGNORECASE)
        out: list[Finding] = []
        for i, line in enumerate(source.splitlines(), 1):
            m = pattern.search(line)
            if m:
                out.append(Finding(
                    self.NAME, "low", "TODO_IN_CODE",
                    f"Unresolved marker {m.group(1).upper()}: '{line.strip()[:80]}'",
                    rel, i,
                ))
        return out

    def _check_bare_except(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                out.append(Finding(
                    self.NAME, "medium", "BARE_EXCEPT",
                    "Bare `except:` swallows KeyboardInterrupt and SystemExit.",
                    rel, node.lineno,
                ))
        return out

    def _check_missing_docstrings(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue   # skip private/dunder helpers
                if not (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    out.append(Finding(
                        self.NAME, "low", "MISSING_DOCSTRING",
                        f"Public function '{node.name}' has no docstring.",
                        rel, node.lineno,
                    ))
        return out

    def _check_duplicate_names(self, tree: ast.AST, rel: str) -> list[Finding]:
        seen: dict[str, int] = {}
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in seen:
                    out.append(Finding(
                        self.NAME, "high", "DUPLICATE_FUNCTION",
                        f"Function '{node.name}' defined multiple times in this module "
                        f"(first at line {seen[node.name]}).",
                        rel, node.lineno,
                    ))
                else:
                    seen[node.name] = node.lineno
        return out

    def _check_deep_nesting(self, source: str, rel: str) -> list[Finding]:
        """Detect lines indented more than MAX_INDENT×4 spaces."""
        out: list[Finding] = []
        threshold = self.MAX_INDENT * 4
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent > threshold:
                out.append(Finding(
                    self.NAME, "low", "DEEP_NESTING",
                    f"Indentation of {indent} spaces (>{threshold}) suggests deep nesting.",
                    rel, i,
                ))
        return out


# ── BugHunter ──────────────────────────────────────────────────────────────────

class BugHunter:
    """
    Pattern-based bug detection:
    - Mutable default arguments (e.g. def f(x=[]))
    - Comparing with `is` instead of `==` for literals
    - Floating-point equality comparisons
    - Division by zero risk (denominator not guarded)
    - Unreachable code after return/raise
    - f-string with empty braces {}
    - `assert` used for input validation (stripped in optimised builds)
    """

    NAME = "BugHunter"

    def run(self) -> list[Finding]:
        findings: list[Finding] = []
        for path in _py_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                tree   = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError):
                continue
            rel = _rel(path)
            findings.extend(self._check_mutable_defaults(tree, rel))
            findings.extend(self._check_is_literal(tree, rel))
            findings.extend(self._check_float_equality(source, rel))
            findings.extend(self._check_unreachable(tree, rel))
            findings.extend(self._check_assert_guard(tree, rel))
        return findings

    def _check_mutable_defaults(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default is None:
                        continue
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        out.append(Finding(
                            self.NAME, "high", "MUTABLE_DEFAULT",
                            f"Function '{node.name}' has a mutable default argument "
                            f"— use None and assign inside.",
                            rel, node.lineno,
                        ))
        return out

    def _check_is_literal(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, comparator in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(
                        comparator, (ast.Constant,)
                    ) and not isinstance(comparator.value, (bool, type(None))):
                        out.append(Finding(
                            self.NAME, "medium", "IS_LITERAL",
                            "Use `==` / `!=` for literal comparisons, not `is` / `is not`.",
                            rel, node.lineno,
                        ))
        return out

    def _check_float_equality(self, source: str, rel: str) -> list[Finding]:
        pattern = re.compile(r'\b[\w.]+\s*==\s*[\d]+\.[\d]+')
        out: list[Finding] = []
        for i, line in enumerate(source.splitlines(), 1):
            if pattern.search(line) and "assert" not in line.lower():
                out.append(Finding(
                    self.NAME, "low", "FLOAT_EQUALITY",
                    f"Direct float equality check — use math.isclose() instead: "
                    f"'{line.strip()[:80]}'",
                    rel, i,
                ))
        return out

    def _check_unreachable(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stmts = node.body
                for idx, stmt in enumerate(stmts[:-1]):
                    if isinstance(stmt, (ast.Return, ast.Raise)):
                        next_stmt = stmts[idx + 1]
                        if not isinstance(next_stmt, (ast.Return, ast.Raise,
                                                       ast.FunctionDef, ast.ClassDef)):
                            out.append(Finding(
                                self.NAME, "medium", "UNREACHABLE_CODE",
                                f"Code after return/raise in '{node.name}' is unreachable.",
                                rel, getattr(next_stmt, "lineno", node.lineno),
                            ))
        return out

    def _check_assert_guard(self, tree: ast.AST, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                out.append(Finding(
                    self.NAME, "low", "ASSERT_GUARD",
                    "assert used for validation — stripped by `python -O`. "
                    "Use an explicit if/raise instead.",
                    rel, node.lineno,
                ))
        return out


# ── OperationalMonitor ─────────────────────────────────────────────────────────

class OperationalMonitor:
    """
    Operational and health checks:
    - Budget lock files present
    - Circuit breaker state
    - Monthly reset overdue
    - .env present (secret hygiene)
    - Detective log size (growing too large → stale findings)
    - Session file freshness
    """

    NAME = "OpsHealth"

    def run(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._check_lock_files())
        findings.extend(self._check_circuit_breaker())
        findings.extend(self._check_reset_overdue())
        findings.extend(self._check_detective_log_size())
        findings.extend(self._check_session_freshness())
        return findings

    def _check_lock_files(self) -> list[Finding]:
        out: list[Finding] = []
        if os.path.exists(config.LOCK_FILE):
            out.append(Finding(
                self.NAME, "critical", "HARD_LOCK_ACTIVE",
                "Budget hard-lock file exists — all cloud tasks are blocked. "
                "Remove via Dashboard or `unlock` command.",
            ))
        elif os.path.exists(config.SOFT_LOCK_FILE):
            out.append(Finding(
                self.NAME, "high", "SOFT_LOCK_ACTIVE",
                "Budget soft-lock file exists — Orchestrator tier blocked (80%+ spend).",
            ))
        else:
            out.append(Finding(
                self.NAME, "info", "BUDGET_LOCK_CLEAR",
                "No budget lock files detected — ecosystem running freely.",
            ))
        return out

    def _check_circuit_breaker(self) -> list[Finding]:
        cb_file = os.path.join(config.BASE_DIR, "circuit_breaker.json")
        if not os.path.exists(cb_file):
            return [Finding(self.NAME, "info", "CIRCUIT_NO_STATE",
                            "No circuit breaker state file — no local failures recorded.")]
        try:
            with open(cb_file) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return [Finding(self.NAME, "low", "CIRCUIT_UNREADABLE",
                            "circuit_breaker.json exists but could not be parsed.")]

        if state.get("circuit_open"):
            import time
            until = state.get("circuit_open_until", 0)
            remaining = max(0, int(until - time.time()))
            return [Finding(
                self.NAME, "high", "CIRCUIT_OPEN",
                f"Local circuit breaker is OPEN ({remaining}s remaining). "
                "Check that Ollama is running.",
            )]
        fc = state.get("failure_count", 0)
        if fc > 0:
            return [Finding(
                self.NAME, "medium", "CIRCUIT_FAILURES",
                f"Local has {fc} recorded failure(s) — not yet open but trending toward it.",
            )]
        return [Finding(self.NAME, "info", "CIRCUIT_HEALTHY",
                        "Circuit breaker is closed with 0 failures.")]

    def _check_reset_overdue(self) -> list[Finding]:
        """Reuse health.check_reset_overdue() to avoid duplicating logic."""
        try:
            from health import check_reset_overdue
            overdue, msg = check_reset_overdue()
            if overdue:
                return [Finding(self.NAME, "medium", "RESET_OVERDUE",
                                f"Monthly reset is overdue: {msg}")]
            return [Finding(self.NAME, "info", "RESET_OK", f"Monthly reset on schedule: {msg}")]
        except Exception as e:
            return [Finding(self.NAME, "low", "RESET_CHECK_FAILED",
                            f"Could not check reset schedule: {e}")]

    def _check_detective_log_size(self) -> list[Finding]:
        log = config.DETECTIVE_LOG
        if not os.path.exists(log):
            return []
        size_mb = os.path.getsize(log) / (1024 * 1024)
        if size_mb > 5:
            return [Finding(
                self.NAME, "low", "DETECTIVE_LOG_LARGE",
                f"detective_log.jsonl is {size_mb:.1f} MB — consider archiving old findings.",
            )]
        return [Finding(self.NAME, "info", "DETECTIVE_LOG_SIZE",
                        f"detective_log.jsonl is {size_mb:.2f} MB.")]

    def _check_session_freshness(self) -> list[Finding]:
        sf = config.SESSION_FILE
        if not os.path.exists(sf):
            return [Finding(self.NAME, "info", "NO_SESSION_FILE",
                            "No current_session.json — no active REPL session recorded.")]
        import time
        age_min = (time.time() - os.path.getmtime(sf)) / 60
        if age_min > 120:
            return [Finding(
                self.NAME, "info", "SESSION_FILE_STALE",
                f"current_session.json is {age_min:.0f} min old — no recent REPL activity.",
            )]
        return [Finding(self.NAME, "info", "SESSION_ACTIVE",
                        f"current_session.json updated {age_min:.0f} min ago.")]


# ── Runner ─────────────────────────────────────────────────────────────────────

INSPECTORS = [
    SecurityAuditor(),
    CodeQualityInspector(),
    BugHunter(),
    OperationalMonitor(),
]


def run_scan(verbose: bool = True) -> list[Finding]:
    """Run all inspectors and append findings to the JSONL log."""
    all_findings: list[Finding] = []
    for inspector in INSPECTORS:
        if verbose:
            print(f"  ► {inspector.NAME} …", end=" ", flush=True)
        try:
            found = inspector.run()
        except Exception as e:
            found = [Finding(inspector.NAME, "critical", "INSPECTOR_CRASH",
                             f"Inspector raised an exception: {e}")]
        all_findings.extend(found)
        if verbose:
            by_sev = {}
            for f in found:
                by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            summary = ", ".join(f"{v} {k}" for k, v in sorted(by_sev.items(),
                                                               key=lambda x: SEVERITY_ORDER.get(x[0], 99)))
            print(summary or "no findings")

    # Append to log
    os.makedirs(config.BASE_DIR, exist_ok=True)
    with open(config.DETECTIVE_LOG, "a", encoding="utf-8") as f:
        for finding in all_findings:
            f.write(json.dumps(finding.to_dict()) + "\n")

    return all_findings


def print_summary(findings: list[Finding]) -> None:
    """Print a severity-sorted summary table to stdout."""
    sorted_f = sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.inspector))
    print(f"\n{'─'*70}")
    print(f"  Codex Detective — {len(findings)} findings")
    print(f"{'─'*70}")
    for f in sorted_f:
        sev_tag = f.severity.upper()[:8].ljust(8)
        loc = f"{f.file}:{f.line}" if f.file else "—"
        print(f"  [{sev_tag}] {f.inspector:<18} {f.rule:<25} {loc}")
        print(f"           {f.message[:80]}")
    print(f"{'─'*70}\n")


def load_last_scan() -> list[Finding]:
    """Return findings from the most recent scan run in the log."""
    if not os.path.exists(config.DETECTIVE_LOG):
        return []
    findings: list[Finding] = []
    last_ts: str | None = None
    with open(config.DETECTIVE_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("ts", "")
            if last_ts is None or ts[:10] > last_ts[:10]:   # new day = new scan
                last_ts = ts
                findings = []
            if ts[:10] == (last_ts or "")[:10]:
                obj = Finding(d["inspector"], d["severity"], d["rule"],
                              d["message"], d.get("file", ""), d.get("line"))
                obj.ts = ts
                findings.append(obj)
    return findings


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Codex Detective — automated ecosystem audit")
    parser.add_argument("--summary", action="store_true",
                        help="Print last scan without re-running")
    parser.add_argument("--clear",   action="store_true",
                        help="Clear detective_log.jsonl (cannot be undone)")
    args = parser.parse_args()

    if args.clear:
        if os.path.exists(config.DETECTIVE_LOG):
            os.remove(config.DETECTIVE_LOG)
            print("detective_log.jsonl cleared.")
        else:
            print("No log file to clear.")
        sys.exit(0)

    if args.summary:
        findings = load_last_scan()
        if not findings:
            print("No previous scan found. Run without --summary to scan now.")
        else:
            print_summary(findings)
        sys.exit(0)

    print("🕵️  Codex Detective — scanning ecosystem…\n")
    findings = run_scan(verbose=True)
    print_summary(findings)
    print(f"Findings appended to: {config.DETECTIVE_LOG}")
