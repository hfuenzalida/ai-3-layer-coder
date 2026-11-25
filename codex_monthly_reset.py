import gzip
import os
import shutil
import pandas as pd
from datetime import datetime, timedelta
import config


def generate_monthly_summary(df: pd.DataFrame) -> str:
    """Calculates final stats before archiving."""
    total_usd = (
        df["estimated_cost"]
        .replace(r"[\$,]", "", regex=True)
        .astype(float)
        .sum()
    )
    total_tokens = df["tokens"].sum()
    total_clp = int(total_usd * config.USD_TO_CLP)

    return (
        f"\n=== CODEX FUTURA: MONTHLY CLOSURE REPORT ===\n"
        f"Date of Reset:          {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Total Tokens Invoiced:  {total_tokens:,}\n"
        f"Total Spend (USD):      ${total_usd:.4f}\n"
        f"Total Spend (CLP):      ${total_clp:,}\n"
        f"============================================\n"
    )


# ── Archive maintenance ────────────────────────────────────────────────────────

def _compress_old_archives(archive_dir: str, months: int = 3) -> list[str]:
    """
    Gzip-compress plain CSV archives that are older than `months` months.
    Returns a list of files compressed in this run.
    """
    cutoff = datetime.now() - timedelta(days=months * 30)
    compressed = []

    for fname in os.listdir(archive_dir):
        if not fname.endswith(".csv"):
            continue
        fpath = os.path.join(archive_dir, fname)
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
        if mtime < cutoff:
            gz_path = fpath + ".gz"
            try:
                with open(fpath, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(fpath)
                compressed.append(fname)
            except Exception as e:
                print(f"⚠ Could not compress {fname}: {e}")

    return compressed


def _purge_old_archives(archive_dir: str, years: int = 2) -> list[str]:
    """
    Delete archive files (both .csv and .csv.gz) older than `years` years.
    Returns a list of deleted filenames.
    """
    cutoff = datetime.now() - timedelta(days=years * 365)
    deleted = []

    for fname in os.listdir(archive_dir):
        if not (fname.endswith(".csv") or fname.endswith(".csv.gz")):
            continue
        fpath = os.path.join(archive_dir, fname)
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
        if mtime < cutoff:
            try:
                os.remove(fpath)
                deleted.append(fname)
            except Exception as e:
                print(f"⚠ Could not delete {fname}: {e}")

    return deleted


def run_maintenance(verbose: bool = True) -> dict:
    """
    Compress archives older than 3 months and purge archives older than 2 years.
    Returns a summary dict with keys 'compressed' and 'deleted'.
    """
    if not os.path.isdir(config.ARCHIVE_DIR):
        if verbose:
            print("Archive directory not found — nothing to maintain.")
        return {"compressed": [], "deleted": []}

    compressed = _compress_old_archives(config.ARCHIVE_DIR, months=3)
    deleted    = _purge_old_archives(config.ARCHIVE_DIR, years=2)

    if verbose:
        if compressed:
            print(f"📦 Compressed {len(compressed)} archive(s): {', '.join(compressed)}")
        else:
            print("📦 No archives needed compression.")
        if deleted:
            print(f"🗑 Purged {len(deleted)} old archive(s): {', '.join(deleted)}")
        else:
            print("🗑 No archives exceeded the 2-year retention limit.")

    return {"compressed": compressed, "deleted": deleted}


# ── Monthly reset ──────────────────────────────────────────────────────────────

def run_reset(run_maintenance_after: bool = True) -> None:
    if not os.path.exists(config.LOG_FILE):
        print("No active log file found to reset.")
        return

    # 1. Load data and generate closure report
    df = pd.read_csv(config.LOG_FILE)
    summary = generate_monthly_summary(df)
    print(summary)

    # 2. Create archive directory
    os.makedirs(config.ARCHIVE_DIR, exist_ok=True)

    # 3. Build archive filename  e.g. usage_2026_03.csv
    last_month = datetime.now().replace(day=1) - timedelta(days=1)
    archive_name = f"usage_{last_month.strftime('%Y_%m')}.csv"
    archive_path = os.path.join(config.ARCHIVE_DIR, archive_name)

    # 4. Move CSV and reset active log
    shutil.move(config.LOG_FILE, archive_path)
    with open(config.LOG_FILE, "w") as f:
        f.write("timestamp,model,provider,tier,tokens,estimated_cost,status,task_summary\n")

    # 5. Save text report
    report_path = os.path.join(
        config.ARCHIVE_DIR, f"report_{last_month.strftime('%Y_%m')}.txt"
    )
    with open(report_path, "w") as f:
        f.write(summary)

    print(f"✅ Reset complete. Archive: {archive_path}")

    # 6. Stamp the reset date so health checks can detect overdue resets
    with open(config.LAST_RESET_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())

    # 7. Optional: compress & purge old archives
    if run_maintenance_after:
        run_maintenance(verbose=True)


if __name__ == "__main__":
    run_reset()
