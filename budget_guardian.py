import time
import pandas as pd
import os
import config

# plyer is optional — desktop notifications are a nice-to-have, not a requirement.
# If the package is missing, the guardian continues in console-only mode.
try:
    from plyer import notification as _plyer_notification
    _PLYER_AVAILABLE = True
except ImportError:
    _plyer_notification = None
    _PLYER_AVAILABLE = False
    print(
        "[budget_guardian] plyer not installed — desktop notifications disabled.\n"
        "  Install with: pip install plyer\n"
        "  Guardian will continue running and applying budget locks normally.",
        flush=True,
    )


def _notify(title: str, message: str, timeout: int = 10) -> None:
    """Send a desktop notification if plyer is available; otherwise print to console."""
    if _PLYER_AVAILABLE:
        try:
            _plyer_notification.notify(
                title=title,
                message=message,
                app_name="Codex Guardian",
                timeout=timeout,
            )
        except Exception as exc:
            # Notification backend crashed (e.g. no display on headless server).
            # Log and continue — the lock files are the real safety mechanism.
            print(f"[budget_guardian] Notification failed: {exc}", flush=True)
    else:
        print(f"[budget_guardian] {title}: {message}", flush=True)

# Cache: only re-run pandas when the CSV has actually changed
_last_mtime:      float = 0.0
_cached_total_clp: float = 0.0

def _read_log_safe() -> pd.DataFrame:
    """Read the CSV safely, retrying up to 3 times to survive mid-write races."""
    for attempt in range(3):
        try:
            df = pd.read_csv(config.LOG_FILE, on_bad_lines="skip")
            if "estimated_cost" in df.columns:
                return df
        except Exception:
            pass
        if attempt < 2:
            time.sleep(0.15)
    return pd.DataFrame()


def _evaluate_budget(total_clp: float):
    """Apply lock/notification logic for a given total spend in CLP."""
    soft_threshold = config.BUDGET_LIMIT_CLP * config.BUDGET_SOFT_THRESHOLD

    if total_clp >= config.BUDGET_LIMIT_CLP:
        if not os.path.exists(config.LOCK_FILE):
            open(config.LOCK_FILE, 'w').close()
            _notify(
                title="🚨 Codex Futura: Budget Hard Locked!",
                message=f"Spend reached ${int(total_clp):,} CLP. All cloud requests blocked.",
            )
    elif total_clp >= soft_threshold:
        if not os.path.exists(config.SOFT_LOCK_FILE):
            open(config.SOFT_LOCK_FILE, 'w').close()
            _notify(
                title="⚠️ Codex Futura: Budget Warning (80%)",
                message=f"Spend at ${int(total_clp):,} CLP. Orchestrator tier blocked, Heavy still active.",
            )
    else:
        if os.path.exists(config.SOFT_LOCK_FILE):
            os.remove(config.SOFT_LOCK_FILE)


def check_budget():
    global _last_mtime, _cached_total_clp

    if not os.path.exists(config.LOG_FILE): return

    current_mtime = os.stat(config.LOG_FILE).st_mtime

    if current_mtime == _last_mtime:
        # CSV unchanged since last check — reuse cached total, skip pandas entirely
        _evaluate_budget(_cached_total_clp)
        return

    df = _read_log_safe()
    if df.empty: return

    df['cost'] = pd.to_numeric(
        df['estimated_cost'].replace(r'[\$,]', '', regex=True),
        errors='coerce'
    ).fillna(0.0)
    total_clp = df['cost'].sum() * config.EXCHANGE_RATE

    _cached_total_clp = total_clp
    _last_mtime       = current_mtime
    _evaluate_budget(total_clp)

if __name__ == "__main__":
    while True:
        check_budget()
        time.sleep(300) # Check every 5 minutes