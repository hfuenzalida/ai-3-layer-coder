"""
Codex Futura — Windows Task Scheduler Setup
--------------------------------------------
Manages two scheduled tasks:

  CodexFutura_MonthlyReset   — runs codex_monthly_reset.py on the 1st of each month at 02:00
  CodexFutura_DailyDetective — runs codex_detective.py every day at 08:00

Usage:
    python setup_scheduler.py                    # register monthly reset
    python setup_scheduler.py --detective        # register daily detective
    python setup_scheduler.py --remove           # unregister monthly reset
    python setup_scheduler.py --remove --detective  # unregister daily detective
    python setup_scheduler.py --status           # show state of both tasks
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()

RESET_TASK_NAME     = "CodexFutura_MonthlyReset"
DETECTIVE_TASK_NAME = "CodexFutura_DailyDetective"

RESET_SCRIPT     = HERE / "codex_monthly_reset.py"
DETECTIVE_SCRIPT = HERE / "codex_detective.py"

PYTHON_EXE = sys.executable


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
    )


def _register(task_name: str, script: Path, schedule: dict) -> None:
    """Generic task registration. `schedule` provides /SC, /D, /MO, /ST keys."""
    if not script.exists():
        print(f"✗ Script not found: {script}")
        sys.exit(1)

    cmd = [
        "/Create",
        "/TN", task_name,
        "/TR", f'"{PYTHON_EXE}" "{script}"',
        "/F",                   # overwrite if already exists
        "/RL", "HIGHEST",
    ]
    for key, val in schedule.items():
        cmd += [key, val]

    result = _schtasks(*cmd)

    if result.returncode == 0:
        print(f"✅  Task '{task_name}' registered.")
        print(f"    Python : {PYTHON_EXE}")
        print(f"    Script : {script}")
        print()
        print(f"    To verify: python setup_scheduler.py --status")
        print(f"    To remove: python setup_scheduler.py --remove{' --detective' if 'Detective' in task_name else ''}")
    else:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"✗  Registration failed: {err}")
        print()
        print("  Common fix: run this script as Administrator.")
        sys.exit(result.returncode)


def register_reset() -> None:
    """Register the monthly reset task (1st of month, 02:00)."""
    _register(
        RESET_TASK_NAME,
        RESET_SCRIPT,
        {"/SC": "MONTHLY", "/D": "1", "/ST": "02:00"},
    )
    print("    Runs   : 1st of each month at 02:00 AM")


def register_detective() -> None:
    """Register the daily detective scan (every day, 08:00)."""
    _register(
        DETECTIVE_TASK_NAME,
        DETECTIVE_SCRIPT,
        {"/SC": "DAILY", "/ST": "08:00"},
    )
    print("    Runs   : every day at 08:00 AM")
    print("    Output : ~/codex_futura/analytics/detective_log.jsonl")
    print("    View   : python codex_detective.py --summary")
    print("             or open the Dashboard → 🕵️ Detective Report")


def remove(task_name: str) -> None:
    """Unregister a scheduled task by name."""
    result = _schtasks("/Delete", "/TN", task_name, "/F")
    if result.returncode == 0:
        print(f"✅  Task '{task_name}' removed.")
    else:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"✗  Could not remove '{task_name}': {err}")
        sys.exit(result.returncode)


def status() -> None:
    """Show the current state of both scheduled tasks."""
    for task_name in (RESET_TASK_NAME, DETECTIVE_TASK_NAME):
        print(f"── {task_name} {'─' * max(0, 50 - len(task_name))}")
        result = _schtasks("/Query", "/TN", task_name, "/FO", "LIST", "/V")
        if result.returncode == 0:
            # Print only the most useful lines
            for line in result.stdout.splitlines():
                for key in ("Status", "Next Run Time", "Last Run Time", "Last Result", "Task To Run"):
                    if line.strip().startswith(key):
                        print(f"  {line.strip()}")
        else:
            print(f"  Not registered — run: python setup_scheduler.py"
                  f"{'' if 'Monthly' in task_name else ' --detective'}")
        print()


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Manage Codex Futura scheduled tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python setup_scheduler.py                  # register monthly reset\n"
            "  python setup_scheduler.py --detective      # register daily detective\n"
            "  python setup_scheduler.py --status         # show both tasks\n"
            "  python setup_scheduler.py --remove         # remove monthly reset\n"
            "  python setup_scheduler.py --remove --detective  # remove daily detective\n"
        ),
    )
    p.add_argument("--detective", action="store_true",
                   help="Target the daily detective task instead of the monthly reset")
    p.add_argument("--remove",    action="store_true",
                   help="Unregister the targeted task")
    p.add_argument("--status",    action="store_true",
                   help="Show current state of both tasks")
    args = p.parse_args()

    if args.status:
        status()
    elif args.remove:
        remove(DETECTIVE_TASK_NAME if args.detective else RESET_TASK_NAME)
    elif args.detective:
        register_detective()
    else:
        register_reset()
