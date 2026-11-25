"""
Codex Futura — Layer Health Checks & Exchange Rate Refresh
-----------------------------------------------------------
Extracted from codex.py so both codex.py and commands.py can import
these utilities without creating a circular dependency.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
import config

from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich import box

console = Console()

BANNER = """
 ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗    ███████╗██╗   ██╗████████╗██╗   ██╗██████╗  █████╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝    ██╔════╝██║   ██║╚══██╔══╝██║   ██║██╔══██╗██╔══██╗
██║     ██║   ██║██║  ██║█████╗   ╚███╔╝     █████╗  ██║   ██║   ██║   ██║   ██║██████╔╝███████║
██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗     ██╔══╝  ██║   ██║   ██║   ██║   ██║██╔══██╗██╔══██║
╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗    ██║     ╚██████╔╝   ██║   ╚██████╔╝██║  ██║██║  ██║
 ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝    ╚═╝      ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
"""


# ── Individual probes ──────────────────────────────────────────────────────────

def check_ollama(url: str, model: str) -> tuple[bool, str]:
    try:
        r = requests.post(
            url,
            json={"model": model, "messages": [{"role": "user", "content": "ping"}]},
            timeout=5,
        )
        return (True, "Online") if r.status_code == 200 else (False, f"HTTP {r.status_code}")
    except requests.exceptions.ConnectionError:
        return False, "Offline — is Ollama running?"
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def check_openrouter(api_key: str) -> tuple[bool, str]:
    if not api_key:
        return False, "No API key in .env"
    try:
        r = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        return (True, "Connected") if r.status_code == 200 else (False, f"HTTP {r.status_code}")
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def check_budget_lock() -> tuple[bool, str]:
    if os.path.exists(config.LOCK_FILE):
        return True, "HARD LOCKED — all cloud blocked"
    if os.path.exists(config.SOFT_LOCK_FILE):
        return True, "SOFT LOCKED — Orchestrator blocked (80% budget)"
    return False, "Clear"


def check_reset_overdue() -> tuple[bool, str]:
    """
    Warn if the monthly log has not been reset in over 35 days.
    Uses LAST_RESET_FILE written by run_reset(); falls back to the log file's
    creation time when no reset has ever been recorded.
    Returns (overdue: bool, message: str).
    """
    _OVERDUE_DAYS = 35

    if os.path.exists(config.LAST_RESET_FILE):
        try:
            with open(config.LAST_RESET_FILE, encoding="utf-8") as f:
                last = datetime.fromisoformat(f.read().strip())
            days = (datetime.now() - last).days
            if days > _OVERDUE_DAYS:
                return True, f"Last reset {days}d ago — run monthly reset"
            return False, f"Last reset {days}d ago"
        except Exception:
            pass

    # No stamp yet — use the log file's creation time as a proxy
    if os.path.exists(config.LOG_FILE):
        try:
            ctime = datetime.fromtimestamp(os.path.getctime(config.LOG_FILE))
            days  = (datetime.now() - ctime).days
            if days > _OVERDUE_DAYS:
                return True, f"Log file {days}d old — run monthly reset"
            return False, f"Log file {days}d old (no reset stamp yet)"
        except Exception:
            pass

    return False, "No log yet"


def check_gitignore() -> tuple[bool, str]:
    """
    Verify that .env is protected by .gitignore.
    Returns (exposed: bool, message: str).
    exposed=True means .env is NOT in .gitignore — a potential key leak risk.
    """
    gitignore_path = os.path.join(os.getcwd(), ".gitignore")
    if not os.path.exists(gitignore_path):
        return True, ".gitignore missing — .env may be committed"
    try:
        with open(gitignore_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        # Accept bare ".env", "*.env", or ".env.*" patterns
        protected = any(p in (".env", "*.env", ".env.*") for p in lines)
        if protected:
            return False, ".env protected"
        return True, ".gitignore exists but .env is not listed"
    except Exception:
        return True, "Could not read .gitignore"


# ── Full health check ──────────────────────────────────────────────────────────

def run_health_check() -> bool:
    """
    Run parallel layer probes and print a Rich status table.
    Returns True if the system is usable, False on a blocking error.
    """
    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print(Rule("[bold]System Health Check[/bold]", style="dim"))
    console.print()

    with console.status("[bold cyan]Checking all layers...[/bold cyan]", spinner="dots"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_ollama     = pool.submit(check_ollama, config.LOCAL_API_URL, config.LOCAL_MODEL_NAME)
            f_openrouter = pool.submit(check_openrouter, config.OPENROUTER_API_KEY)
            ollama_ok,     ollama_msg     = f_ollama.result()
            openrouter_ok, openrouter_msg = f_openrouter.result()
        budget_locked, budget_msg   = check_budget_lock()
        env_exposed,   env_msg      = check_gitignore()
        reset_overdue, reset_msg    = check_reset_overdue()

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold dim")
    table.add_column("Layer",  style="bold", width=22)
    table.add_column("Model",  width=34)
    table.add_column("Status", width=36)
    table.add_row(
        "Layer 1 — Local", config.LOCAL_MODEL_NAME,
        f"[bold green]✓ {ollama_msg}[/bold green]" if ollama_ok
        else f"[bold red]✗ {ollama_msg}[/bold red]",
    )
    table.add_row(
        "Layer 2 — Cloud", "Qwen-2.5-Coder-32B",
        f"[bold green]✓ {openrouter_msg}[/bold green]" if openrouter_ok
        else f"[bold red]✗ {openrouter_msg}[/bold red]",
    )
    table.add_row("Layer 3 — Orchestrator", "Claude (this session)", "[bold green]✓ Active[/bold green]")
    table.add_row(
        "Budget Guardian", "—",
        f"[bold red]⚠ {budget_msg}[/bold red]" if budget_locked
        else f"[bold green]✓ {budget_msg}[/bold green]",
    )
    table.add_row(
        "Secret Safety", ".env",
        f"[bold yellow]⚠ {env_msg}[/bold yellow]" if env_exposed
        else f"[bold green]✓ {env_msg}[/bold green]",
    )
    table.add_row(
        "Monthly Reset", "log file",
        f"[bold yellow]⚠ {reset_msg}[/bold yellow]" if reset_overdue
        else f"[bold green]✓ {reset_msg}[/bold green]",
    )
    console.print(table)
    console.print()

    if not openrouter_ok:
        console.print("[bold red]✗ OpenRouter unreachable. Check OPENROUTER_API_KEY in .env[/bold red]")
        return False
    if not ollama_ok:
        console.print("[yellow]⚠ Ollama offline. Local tasks will escalate to cloud.[/yellow]")
    if budget_locked:
        console.print("[bold red]⚠ Budget lock active. Cloud tasks are blocked.[/bold red]")
    if env_exposed:
        console.print(
            f"[bold yellow]⚠ Secret Safety: {env_msg}. "
            f"Add '.env' to .gitignore before pushing to git.[/bold yellow]"
        )
    if reset_overdue:
        console.print(
            f"[bold yellow]⚠ Monthly Reset: {reset_msg}. "
            f"Run `archive` in the REPL, or: python codex_monthly_reset.py[/bold yellow]"
        )
    return True


# ── Exchange rate ──────────────────────────────────────────────────────────────

def refresh_exchange_rate() -> None:
    """
    Hit open.er-api.com and update config.USD_TO_CLP in-memory.
    Falls back silently to the hardcoded value on any error.
    """
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=4)
        if r.status_code == 200:
            clp = r.json().get("rates", {}).get("CLP")
            if clp and isinstance(clp, (int, float)) and clp > 0:
                config.USD_TO_CLP    = float(clp)
                config.EXCHANGE_RATE = float(clp)
                console.print(f"[dim]💱 Exchange rate updated: 1 USD = {clp:,.2f} CLP[/dim]")
                return
    except Exception:
        pass
    console.print(f"[dim]💱 Exchange rate: using hardcoded 1 USD = {config.USD_TO_CLP:,.2f} CLP[/dim]")
