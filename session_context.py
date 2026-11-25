"""
Codex Futura — Session Context
--------------------------------
Single dataclass that carries all mutable REPL state.
Passed by reference to every command handler so they can update
counters and last_content without relying on nonlocal or globals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator import Orchestrator

from session_stats import SessionStats


@dataclass
class SessionContext:
    session:      SessionStats
    orchestrator: "Orchestrator"
    task_counter: int        = 0
    last_content: str | None = None
    # ── Run-mode flags (set from CLI args, propagated to commands) ────────────
    dry_run:  bool = False   # skip all API calls; log routing decisions only
    no_cloud: bool = False   # suppress cloud escalation; local tier only
