"""
Codex Futura — 3-Layer Coder Ecosystem
Entry point. Run with: python codex.py

All command logic lives in commands.py.
All health-check / exchange-rate logic lives in health.py.
This file owns only: startup, the REPL loop, and graceful shutdown.

Flags
-----
  --dry-run    Skip all API calls; print routing decisions only. Safe for
               testing configuration and keyword routing without spending tokens.
  --no-cloud   Local (Ollama) tier only. Cloud escalation is suppressed even
               when local fails or the router chooses Heavy/Orchestrator.
  --version    Print version and exit.
"""
import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt

from __version__ import __version__
from health import run_health_check, refresh_exchange_rate
from session_context import SessionContext
from session_stats import SessionStats, log_session_stats_to_csv, export_session_json
from commands import dispatch

load_dotenv()
console = Console()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="codex",
        description="Codex Futura — 3-layer AI coding assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print routing decisions only — no API calls, no token spend.",
    )
    parser.add_argument(
        "--no-cloud",
        action="store_true",
        help="Local (Ollama) tier only — suppress all cloud escalation.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Codex Futura {__version__}",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    from orchestrator import Orchestrator

    # ── Mode banner ───────────────────────────────────────────────────────────
    if args.dry_run:
        console.print(
            f"[bold yellow]⚠  DRY-RUN mode — no API calls will be made.[/bold yellow]"
        )
    if args.no_cloud:
        console.print(
            f"[bold yellow]⚠  NO-CLOUD mode — local (Ollama) tier only.[/bold yellow]"
        )

    # ── Startup ───────────────────────────────────────────────────────────────
    if not run_health_check():
        sys.exit(1)

    refresh_exchange_rate()

    # ── Session initialisation ────────────────────────────────────────────────
    orchestrator = Orchestrator()
    session_id   = datetime.now().strftime("%Y%m%d-%H%M%S")
    session      = SessionStats(session_id=session_id, start_time=datetime.now().isoformat())
    ctx          = SessionContext(
        session=session,
        orchestrator=orchestrator,
        dry_run=args.dry_run,
        no_cloud=args.no_cloud,
    )

    # Show help once, then drop into the REPL
    dispatch("help", ctx)
    console.print(f"[dim]Codex Futura {__version__} — Session ID: {session_id}[/dim]\n")

    # ── REPL loop ─────────────────────────────────────────────────────────────
    while True:
        try:
            task = Prompt.ask("[bold cyan]codex[/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            _shutdown(ctx)
            break

        if not task:
            continue

        # dispatch() returns False when the user types exit/quit
        if not dispatch(task, ctx):
            _shutdown(ctx)
            break


def _shutdown(ctx: SessionContext) -> None:
    """Log and export session stats, then print goodbye."""
    if ctx.session.task_count > 0:
        log_session_stats_to_csv(ctx.session)
        export_session_json(ctx.session)
        console.print(f"\n[dim]Session logged. ID: {ctx.session.session_id}[/dim]")
    console.print("[dim]Goodbye.[/dim]")


if __name__ == "__main__":
    main()
