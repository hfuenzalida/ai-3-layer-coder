"""
Codex Futura — REPL Command Handlers
--------------------------------------
One function per command category. Every handler receives a SessionContext
(mutable session state) and an args string (everything after the keyword).

The public `dispatch(task, ctx)` function is the single entry point called
by the main REPL loop in codex.py — no command logic lives there any more.
"""
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich import box

import cache as response_cache
import config
import templates as tpl_module
from session_context import SessionContext
from session_stats import TaskStats, calculate_cost, export_session_json

console = Console()


# ── Help ──────────────────────────────────────────────────────────────────────

def cmd_help(ctx: SessionContext, args: str) -> None:
    console.print()
    console.print(Panel(
        "[bold]Commands:[/bold]\n"
        "  Type any task and press Enter to execute\n\n"
        "[bold dim]Workflow[/bold dim]\n"
        "  [cyan]plan <objective>[/cyan]              — architect + execute a multi-step workflow\n"
        "  [cyan]batch <file>[/cyan]                  — run tasks line-by-line from a text file\n"
        "  [cyan]save <path>[/cyan]                   — save last response to a file\n\n"
        "[bold dim]Templates[/bold dim]\n"
        "  [cyan]save-template <name> <text>[/cyan]   — save a reusable prompt template\n"
        "  [cyan]template <name> [key=value ...][/cyan] — expand and run a template\n"
        "  [cyan]templates[/cyan]                     — list all saved templates\n"
        "  [cyan]del-template <name>[/cyan]            — delete a template\n\n"
        "[bold dim]System[/bold dim]\n"
        "  [cyan]status[/cyan]                        — re-run health check\n"
        "  [cyan]stats[/cyan]                         — show session statistics\n"
        "  [cyan]cache[/cyan]                         — show response cache stats\n"
        "  [cyan]cache clear[/cyan]                   — wipe response cache\n"
        "  [cyan]archive[/cyan]                       — compress old archives & purge 2yr+ files\n"
        "  [cyan]export[/cyan]                        — export session to JSON\n"
        "  [cyan]dashboard[/cyan]                     — open analytics in browser\n"
        "  [cyan]unlock[/cyan]                        — remove budget lock\n"
        "  [cyan]help[/cyan]                          — show this menu\n"
        "  [cyan]exit[/cyan]                          — quit",
        title="[bold dim]Help[/bold dim]",
        border_style="dim",
        padding=(0, 2),
    ))
    console.print()


# ── System ────────────────────────────────────────────────────────────────────

def cmd_status(ctx: SessionContext, args: str) -> None:
    from health import run_health_check
    run_health_check()


def cmd_unlock(ctx: SessionContext, args: str) -> None:
    removed = []
    for label, path in [("hard lock", config.LOCK_FILE), ("soft lock", config.SOFT_LOCK_FILE)]:
        if os.path.exists(path):
            os.remove(path)
            removed.append(label)
    if removed:
        console.print(f"[bold green]✓ Removed: {', '.join(removed)}. Cloud tasks re-enabled.[/bold green]")
    else:
        console.print("[dim]No lock files found — already unlocked.[/dim]")


def cmd_dashboard(ctx: SessionContext, args: str) -> None:
    console.print("[dim]Opening dashboard...[/dim]")
    subprocess.Popen(
        [r".venv\Scripts\streamlit.exe", "run", "dashboard.py"],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def cmd_archive(ctx: SessionContext, args: str) -> None:
    from codex_monthly_reset import run_maintenance
    result = run_maintenance(verbose=False)
    c, d = result["compressed"], result["deleted"]
    console.print()
    console.print(f"[green]📦 Compressed:[/green] {', '.join(c)}" if c else "[dim]📦 Nothing to compress.[/dim]")
    console.print(f"[yellow]🗑 Purged:[/yellow] {', '.join(d)}" if d else "[dim]🗑 Nothing to purge.[/dim]")
    console.print()


# ── Cache ─────────────────────────────────────────────────────────────────────

def cmd_cache(ctx: SessionContext, args: str) -> None:
    info    = response_cache.stats()
    expired = response_cache.clear_expired()
    tbl = Table(box=box.ROUNDED, show_header=True, header_style="bold dim")
    tbl.add_column("Tier",          style="cyan")
    tbl.add_column("Entries",       justify="right")
    tbl.add_column("Tokens cached", justify="right")
    for row in info["by_tier"]:
        tbl.add_row(row[0], str(row[1]), f"{row[2]:,}")
    console.print()
    console.print(tbl)

    # Session hit-rate line
    hr = info.get("hit_rate")
    if hr is None:
        hr_str = "[dim]no lookups yet[/dim]"
    else:
        color  = "green" if hr >= 0.5 else "yellow" if hr >= 0.2 else "red"
        hr_str = f"[bold {color}]{hr * 100:.1f}%[/bold {color}] ({info['hits']} hits / {info['hits'] + info['misses']} lookups)"
    console.print(f"[dim]  Total: {info['total']} entries · {expired} expired pruned · Session hit rate: [/dim]{hr_str}\n")


def cmd_cache_clear(ctx: SessionContext, args: str) -> None:
    n = response_cache.clear_all()
    console.print(f"[bold green]✓ Cache wiped — {n} entries removed.[/bold green]\n")


# ── Stats / Export ────────────────────────────────────────────────────────────

def cmd_stats(ctx: SessionContext, args: str) -> None:
    s = ctx.session
    console.print()
    console.print(Rule("[bold cyan]Session Statistics[/bold cyan]", style="dim"))
    console.print()

    summary = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    for label, value in [
        ("Task Count",    str(s.task_count)),
        ("Total Tokens",  f"{s.total_tokens:,}"),
        ("Total Cost",    f"${s.total_cost:.4f}"),
        ("Avg Cost/Task", f"${s.total_cost / max(s.task_count, 1):.4f}"),
    ]:
        summary.add_row(f"[bold]{label}[/bold]", f"[cyan]{value}[/cyan]")
    console.print(summary)
    console.print()

    for title, data in [("By Model", s.by_model), ("By Tier", s.by_tier), ("By Provider", s.by_provider)]:
        if not data:
            continue
        console.print(f"[bold]{title}:[/bold]")
        tbl = Table(box=box.ROUNDED, show_header=True, header_style="bold dim")
        tbl.add_column(title.split()[-1], style="cyan")
        tbl.add_column("Tokens",     justify="right")
        tbl.add_column("Cost (USD)", justify="right")
        tbl.add_column("Tasks",      justify="right")
        for key, st in sorted(data.items(), key=lambda x: x[1]["cost"], reverse=True):
            tbl.add_row(key, f"{st['tokens']:,}", f"${st['cost']:.4f}", str(st["count"]))
        console.print(tbl)
        console.print()


def cmd_export(ctx: SessionContext, args: str) -> None:
    path = export_session_json(ctx.session)
    console.print(f"[bold green]✓ Session exported to:[/bold green] {path}\n")


# ── File save ─────────────────────────────────────────────────────────────────

def cmd_save(ctx: SessionContext, args: str) -> None:
    path = args.strip()
    if not path:
        console.print("[yellow]Usage: save <path>[/yellow]\n")
        return
    if ctx.last_content is None:
        console.print("[yellow]Nothing to save — run a task first.[/yellow]\n")
        return
    from task_executor import save_file
    ok, msg = save_file(path, ctx.last_content)
    style = "green" if ok else "red"
    icon  = "✓"    if ok else "✗"
    console.print(f"[bold {style}]{icon}[/bold {style}] {msg}\n")


# ── Workflow: plan ────────────────────────────────────────────────────────────

def cmd_plan(ctx: SessionContext, args: str) -> None:
    objective = args.strip() or Prompt.ask("[bold cyan]Objective[/bold cyan]").strip()
    if not objective:
        return

    # ── Mode guards ───────────────────────────────────────────────────────────
    if ctx.dry_run:
        console.print(
            f"[bold yellow][DRY-RUN][/bold yellow] Would execute multi-step workflow:\n"
            f"[dim]  Objective: {objective}[/dim]\n"
            f"[dim]  generate_plan() → Orchestrator tier → task loop via Heavy tier[/dim]\n"
            f"[dim]  No API calls made.[/dim]\n"
        )
        return
    if ctx.no_cloud:
        console.print(
            "[bold red]✗[/bold red] [yellow]`plan` requires cloud tiers (Orchestrator for "
            "planning, Heavy for execution). --no-cloud blocks all cloud calls.\n"
            "  Remove --no-cloud or run individual local tasks instead.[/yellow]\n"
        )
        return

    from task_executor import WorkflowEngine
    engine  = WorkflowEngine(orchestrator=ctx.orchestrator)
    results = engine.execute_workflow(objective)
    for r in results:
        ctx.task_counter += 1
        ctx.session.tasks.append(TaskStats(
            task_id=ctx.task_counter, task_summary=r["summary"],
            model=r["model"], tier=r["tier"], provider=r["provider"],
            tokens=r["tokens"], cost_usd=calculate_cost(r["model"], r["tokens"]),
            timestamp=r["timestamp"], status=r["status"],
        ))
    if results:
        s = ctx.session
        console.print(
            f"[dim]Session Total: {s.total_tokens:,} tokens | ${s.total_cost:.4f} | Tasks: {s.task_count}[/dim]\n"
        )
    _flush_session(ctx)


# ── Workflow: batch ───────────────────────────────────────────────────────────

def cmd_batch(ctx: SessionContext, args: str) -> None:
    path = args.strip()
    if not os.path.isfile(path):
        console.print(f"[bold red]✗ File not found: {path}[/bold red]\n")
        return
    with open(path, encoding="utf-8") as fh:
        tasks = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    console.print(f"[bold]Batch: {len(tasks)} tasks from [cyan]{path}[/cyan][/bold]\n")
    timeout = config.BATCH_TASK_TIMEOUT_SEC
    for idx, btask in enumerate(tasks, 1):
        console.print(Rule(f"[bold]Batch {idx}/{len(tasks)}[/bold]", style="dim"))
        console.print(f"[dim]{btask}[/dim]")
        # Each task runs in a worker thread so a hung call doesn't stall the
        # whole file. The thread itself can't be forcibly killed, but the main
        # loop moves on and the worker exits when its own network timeout fires.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_and_track, ctx, btask)
            try:
                future.result(timeout=timeout)
            except FuturesTimeout:
                console.print(
                    f"[bold yellow]⏱ Task {idx} timed out after {timeout}s — skipping.[/bold yellow]"
                )
                ctx.task_counter += 1
                ctx.session.tasks.append(TaskStats(
                    task_id=ctx.task_counter, task_summary=btask[:60],
                    model="unknown", tier="Unknown", tokens=0, cost_usd=0.0,
                    timestamp=datetime.now().isoformat(), status="Timed out",
                ))
                console.print()
    s = ctx.session
    console.print(
        f"[dim]Batch complete — Session: {s.total_tokens:,} tokens | ${s.total_cost:.4f}[/dim]\n"
    )


# ── Templates ─────────────────────────────────────────────────────────────────

def cmd_templates(ctx: SessionContext, args: str) -> None:
    tpls = tpl_module.list_all()
    if not tpls:
        console.print("[dim]No templates saved yet. Use: save-template <name> <text>[/dim]\n")
        return
    tbl = Table(box=box.ROUNDED, show_header=True, header_style="bold dim")
    tbl.add_column("Name", style="cyan")
    tbl.add_column("Preview", style="dim")
    for tpl in tpls:
        preview = tpl["text"][:72].replace("\n", " ")
        if len(tpl["text"]) > 72:
            preview += "…"
        tbl.add_row(tpl["name"], preview)
    console.print()
    console.print(tbl)
    console.print()


def cmd_save_template(ctx: SessionContext, args: str) -> None:
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        console.print("[yellow]Usage: save-template <name> <text>[/yellow]\n")
        return
    name, text = parts[0], parts[1]
    tpl_module.save(name, text)
    console.print(f"[bold green]✓ Template '{name}' saved.[/bold green]\n")


def cmd_del_template(ctx: SessionContext, args: str) -> None:
    name = args.strip()
    if tpl_module.delete(name):
        console.print(f"[bold green]✓ Template '{name}' deleted.[/bold green]\n")
    else:
        console.print(f"[yellow]Template '{name}' not found.[/yellow]\n")


def cmd_template(ctx: SessionContext, args: str) -> None:
    parts = args.strip().split(None, 1)
    if not parts:
        console.print("[yellow]Usage: template <name> [key=value ...][/yellow]\n")
        return
    name, kwargs_str = parts[0], (parts[1] if len(parts) > 1 else "")
    try:
        prompt = tpl_module.render(name, **tpl_module.parse_kwargs(kwargs_str))
        console.print(f"[dim]▶ Template '{name}' → [/dim]{prompt[:80]}{'…' if len(prompt) > 80 else ''}\n")
        _run_and_track(ctx, prompt)
    except ValueError as ve:
        console.print(f"[bold red]✗ {ve}[/bold red]\n")


# ── Session file flush (monitor footer) ──────────────────────────────────────

def _flush_session(ctx: SessionContext) -> None:
    """
    Write live session totals to SESSION_FILE so the floating monitor can
    display current-session stats instead of the cumulative monthly log.
    Called after every task completion (success or failure).
    """
    try:
        s = ctx.session
        os.makedirs(os.path.dirname(config.SESSION_FILE), exist_ok=True)
        with open(config.SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": s.session_id,
                "tokens":     s.total_tokens,
                "cost":       s.total_cost,
                "tasks":      s.task_count,
            }, f)
    except Exception:
        pass


# ── Shared task execution ─────────────────────────────────────────────────────

def _run_and_track(ctx: SessionContext, task: str) -> None:
    """Execute one task, append stats, print summary line. Used by default, batch, template."""
    ctx.task_counter += 1

    # ── --dry-run: show routing decision only, make no API call ──────────────
    if ctx.dry_run:
        duty, target, reason = ctx.orchestrator.router.route_request(task)
        console.print(
            f"[bold yellow][DRY-RUN][/bold yellow] Would route to "
            f"[cyan]{duty.upper()}[/cyan] → [cyan]{target}[/cyan]\n"
            f"[dim]  {reason}[/dim]\n"
        )
        return

    # ── --no-cloud: run only local tier; on failure, print warning (no escalation) ──
    if ctx.no_cloud:
        local_out = ctx.orchestrator._try_local(task, task[:60])
        if local_out is None:
            console.print(
                "[bold red]✗[/bold red] [yellow]Local tier unavailable and --no-cloud "
                "is set — skipping cloud escalation.[/yellow]\n"
            )
            ctx.session.tasks.append(TaskStats(
                task_id=ctx.task_counter, task_summary=task[:60],
                model="local", tier="Light", provider="Local (Ollama)",
                tokens=0, cost_usd=0.0,
                timestamp=datetime.now().isoformat(), status="Skipped (no-cloud)",
            ))
            _flush_session(ctx)
            return
        result, status = local_out
    else:
        result, status = ctx.orchestrator.execute_task(task)

    if result:
        tokens   = result.get("tokens",   0)
        model    = result.get("model",    "unknown")
        tier     = result.get("tier",     "Unknown")
        provider = result.get("provider", "")
        cost     = calculate_cost(model, tokens)
        ctx.last_content = result["choices"][0]["message"]["content"]
        ctx.session.tasks.append(TaskStats(
            task_id=ctx.task_counter, task_summary=task[:60],
            model=model, tier=tier, provider=provider,
            tokens=tokens, cost_usd=cost,
            timestamp=datetime.now().isoformat(), status="Success",
        ))
        console.print(f"[bold green]✓[/bold green] [dim]{status}[/dim]")
        console.print(f"[dim]  Tokens: {tokens:,} | Cost: ${cost:.4f} | Model: {model}[/dim]")
    else:
        ctx.session.tasks.append(TaskStats(
            task_id=ctx.task_counter, task_summary=task[:60],
            model="unknown", tier="Unknown", tokens=0, cost_usd=0.0,
            timestamp=datetime.now().isoformat(), status="Failed",
        ))
        console.print(f"[bold red]✗[/bold red] {status}")
    console.print()
    if ctx.session.task_count > 0:
        s = ctx.session
        console.print(
            f"[dim]Session Total: {s.total_tokens:,} tokens | ${s.total_cost:.4f} | Tasks: {s.task_count}[/dim]"
        )
        console.print()
    _flush_session(ctx)


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch(task: str, ctx: SessionContext) -> bool:
    """
    Route a raw REPL input string to the correct handler.
    Returns False if the user typed 'exit'/'quit', True to continue the loop.
    """
    t  = task.strip()
    tl = t.lower()

    if tl in ("exit", "quit"):
        return False

    # Exact-match commands (order matters: 'cache clear' before 'cache')
    _EXACT: dict[str, object] = {
        "help":        cmd_help,
        "status":      cmd_status,
        "stats":       cmd_stats,
        "cache clear": cmd_cache_clear,
        "cache":       cmd_cache,
        "unlock":      cmd_unlock,
        "dashboard":   cmd_dashboard,
        "archive":     cmd_archive,
        "export":      cmd_export,
        "templates":   cmd_templates,
    }
    if tl in _EXACT:
        _EXACT[tl](ctx, "")
        return True

    # Prefix commands (keyword + space + args)
    _PREFIX: list[tuple[str, object, int]] = [
        ("plan ",          cmd_plan,          5),
        ("plan",           cmd_plan,          4),   # bare "plan" → prompt for objective
        ("save ",          cmd_save,          5),
        ("batch ",         cmd_batch,         6),
        ("save-template ", cmd_save_template, 14),
        ("del-template ",  cmd_del_template,  13),
        ("template ",      cmd_template,      9),
    ]
    for prefix, handler, cut in _PREFIX:
        if tl == prefix.strip() or tl.startswith(prefix):
            handler(ctx, t[cut:])
            return True

    # Default: treat as a free-form task
    console.print()
    _run_and_track(ctx, t)
    return True
