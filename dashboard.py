import gzip
import io
import streamlit as st
import pandas as pd
import plotly.express as px
import os
from datetime import datetime
import config

st.set_page_config(page_title="Codex Futura: Strategic Analytics", layout="wide")

# --- SIDEBAR: Navigation & Settings ---
st.sidebar.title("🛸 Codex Control")
view_mode = st.sidebar.radio("Select View Mode", ["Live Sprint", "Historical Archives"])

st.sidebar.divider()
st.sidebar.subheader("🇨🇱 Currency Settings")
usd_to_clp = st.sidebar.number_input("Exchange Rate (USD to CLP)", value=config.USD_TO_CLP, step=1.0)
show_clp = st.sidebar.toggle("Show values in CLP", value=True)

def format_money(amount):
    if show_clp: return f"${int(amount * usd_to_clp):,}"
    return f"${amount:.4f}"

# --- DATA LOADING LOGIC ---
def _read_archive(file_path: str) -> pd.DataFrame:
    """Read a CSV archive — handles both plain .csv and gzip-compressed .csv.gz."""
    if file_path.endswith(".gz"):
        with gzip.open(file_path, "rb") as gz:
            return pd.read_csv(io.BytesIO(gz.read()), on_bad_lines="skip")
    return pd.read_csv(file_path, on_bad_lines="skip")


def get_data(mode):
    if mode == "Live Sprint":
        if os.path.exists(config.LOG_FILE):
            return pd.read_csv(config.LOG_FILE, on_bad_lines="skip"), "Current Month (Live)"
        return pd.DataFrame(), "No Live Data"
    else:
        if not os.path.exists(config.ARCHIVE_DIR) or not os.listdir(config.ARCHIVE_DIR):
            return pd.DataFrame(), "No Archives Found"

        # Show both plain CSV and gzip-compressed archives
        archives = sorted(
            [f for f in os.listdir(config.ARCHIVE_DIR)
             if f.endswith(".csv") or f.endswith(".csv.gz")],
            reverse=True,
        )
        if not archives:
            return pd.DataFrame(), "No Archives Found"
        selected_file = st.sidebar.selectbox("Select Archived Month", archives)
        file_path = os.path.join(config.ARCHIVE_DIR, selected_file)
        return _read_archive(file_path), f"Archive: {selected_file}"

df, view_label = get_data(view_mode)

# --- DASHBOARD UI ---
st.title(f"📊 {view_label}")

if os.path.exists(config.LOCK_FILE):
    st.error("🚨 **BUDGET HARD LOCKED: All cloud tasks are blocked.** Monthly limit exceeded.", icon="🛑")
    if st.button("Remove Hard Lock", type="primary"):
        os.remove(config.LOCK_FILE)
        st.rerun()

if os.path.exists(config.SOFT_LOCK_FILE):
    st.warning(
        "⚠️ **BUDGET SOFT LOCKED (80%+): Orchestrator tier blocked.** "
        "Heavy (Qwen 32B) tasks still active.",
        icon="⚠️",
    )
    if st.button("Remove Soft Lock"):
        os.remove(config.SOFT_LOCK_FILE)
        st.rerun()

if view_mode == "Live Sprint":
    st.caption("Real-time operational costs and performance for Codex Futura.")
else:
    st.caption("Audited historical data for business intelligence and tax reporting.")

st.divider()

if df.empty:
    st.info("No data available for the selected view.")
else:
    # --- PRE-PROCESSING ---
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['cost_usd'] = df['estimated_cost'].replace('[\$,]', '', regex=True).astype(float)
    
    # --- KPI CALCULATIONS ---
    total_spent_usd = df['cost_usd'].sum()
    total_tokens    = df['tokens'].sum()

    # Savings: compare actual cloud spend against what it would have cost
    # if every cloud task had run through Claude Sonnet (the most expensive tier).
    # Local (Light) tasks are excluded — they were never competing with Sonnet
    # pricing, so counting them would inflate the savings figure.
    SONNET_PRICE_PER_1M = config.MODEL_PRICING.get("anthropic/claude-3.5-sonnet", 15.0)
    cloud_df = (
        df[df['tier'].isin(['Heavy', 'Orchestrator'])]
        if 'tier' in df.columns else df
    )
    theoretical_cost_usd = (cloud_df['tokens'].sum() / 1_000_000) * SONNET_PRICE_PER_1M
    savings_usd          = theoretical_cost_usd - cloud_df['cost_usd'].sum()

    success_rate = (df['status'] == 'Success').mean() * 100
    failure_count = len(df[df['status'] != 'Success'])

    # Cache hit rate — any row logged with status "Cache" was a cache hit
    total_tasks  = len(df)
    cache_hits   = int((df['status'] == 'Cache').sum()) if 'status' in df.columns else 0
    cache_rate   = (cache_hits / total_tasks * 100) if total_tasks > 0 else 0.0

    # --- METRIC CARDS ---
    cols = st.columns(6)
    cols[0].metric("Total Spend",             format_money(total_spent_usd))
    cols[1].metric("Total Tokens",            f"{total_tokens:,}")
    cols[2].metric("Cloud Savings (vs Sonnet)", format_money(savings_usd),
                   help="How much less cloud tasks cost vs routing everything through Claude Sonnet")
    cols[3].metric("Success Rate",            f"{success_rate:.1f}%")
    cols[4].metric("Failures",                failure_count)
    cols[5].metric("Cache Hit Rate",          f"{cache_rate:.1f}%",
                   help=f"{cache_hits} of {total_tasks} tasks served from cache")

    st.divider()

    # --- CHARTS ---
    col_left, col_right = st.columns(2)
    
    with col_left:
        # Spending Trend
        df['cost_local'] = df['cost_usd'] * usd_to_clp if show_clp else df['cost_usd']
        daily_trend = df.groupby(df['timestamp'].dt.date)['cost_local'].sum().reset_index()
        fig_trend = px.line(daily_trend, x='timestamp', y='cost_local', title="Daily Spend Pattern", markers=True)
        st.plotly_chart(fig_trend, use_container_width=True)

    with col_right:
        # Model Usage Split
        fig_pie = px.pie(df, names='model', values='tokens', title="Token Distribution by Model", hole=0.5)
        st.plotly_chart(fig_pie, use_container_width=True)

    # --- DETAILED LOG ---
    with st.expander("🔍 View Raw Transaction Log"):
        st.dataframe(df.sort_values('timestamp', ascending=False), use_container_width=True)

# ── 🕵️ Detective Report ────────────────────────────────────────────────────────

st.divider()
st.header("🕵️ Detective Report")
st.caption(
    "Automated security, code-quality, bug-hunt, and operational health findings. "
    "Run `python codex_detective.py` to refresh."
)

import json as _json

_SEVERITY_COLOR = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🔵",
    "info":     "⚪",
}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _load_detective_findings(log_path: str) -> list[dict]:
    """Load findings from the most recent scan day in the JSONL log."""
    if not os.path.exists(log_path):
        return []
    findings: list[dict] = []
    last_day: str | None = None
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            day = d.get("ts", "")[:10]
            if last_day is None or day > last_day:
                last_day = day
                findings = []
            if day == last_day:
                findings.append(d)
    return findings


_detective_findings = _load_detective_findings(config.DETECTIVE_LOG)

if not _detective_findings:
    st.info(
        "No detective findings yet. Run `python codex_detective.py` from the project root "
        "to perform the first scan.",
        icon="🕵️",
    )
else:
    _last_ts = _detective_findings[0].get("ts", "")[:19].replace("T", " ") if _detective_findings else "—"
    st.caption(f"Last scan: **{_last_ts} UTC** — {len(_detective_findings)} total findings")

    # ── Severity summary cards ──────────────────────────────────────────────────
    _sev_counts: dict[str, int] = {}
    for f in _detective_findings:
        s = f.get("severity", "info")
        _sev_counts[s] = _sev_counts.get(s, 0) + 1

    _sev_cols = st.columns(5)
    for col, sev in zip(_sev_cols, ["critical", "high", "medium", "low", "info"]):
        cnt = _sev_counts.get(sev, 0)
        icon = _SEVERITY_COLOR[sev]
        col.metric(f"{icon} {sev.capitalize()}", cnt)

    st.divider()

    # ── Inspector tabs ──────────────────────────────────────────────────────────
    _inspectors = ["SecurityAuditor", "CodeQuality", "BugHunter", "OpsHealth"]
    _tabs = st.tabs([f"🔒 Security", f"🧹 Code Quality", f"🐛 Bug Hunt", f"🏥 Ops Health"])

    for tab, inspector_name in zip(_tabs, _inspectors):
        with tab:
            _tab_findings = [
                f for f in _detective_findings
                if f.get("inspector") == inspector_name
            ]
            if not _tab_findings:
                st.success("No findings for this inspector in the last scan.", icon="✅")
                continue

            # Sort by severity
            _tab_findings.sort(key=lambda x: _SEVERITY_ORDER.get(x.get("severity", "info"), 99))

            # Filter control
            _severities = sorted(
                {f.get("severity", "info") for f in _tab_findings},
                key=lambda s: _SEVERITY_ORDER.get(s, 99),
            )
            _selected_sev = st.multiselect(
                "Filter by severity",
                options=_severities,
                default=[s for s in _severities if s != "info"],
                key=f"sev_filter_{inspector_name}",
            )
            _shown = [f for f in _tab_findings
                      if f.get("severity") in _selected_sev] if _selected_sev else _tab_findings

            # Findings table
            _rows = []
            for f in _shown:
                sev = f.get("severity", "info")
                _rows.append({
                    "Severity":  f"{_SEVERITY_COLOR.get(sev, '')} {sev}",
                    "Rule":      f.get("rule", ""),
                    "File":      f.get("file", "—") or "—",
                    "Line":      f.get("line") or "—",
                    "Message":   f.get("message", "")[:120],
                })

            if _rows:
                _det_df = pd.DataFrame(_rows)
                st.dataframe(_det_df, use_container_width=True, hide_index=True)
            else:
                st.info("No findings match the selected severity filters.")

    # ── Historical trend ────────────────────────────────────────────────────────
    with st.expander("📈 Historical Finding Trend"):
        if os.path.exists(config.DETECTIVE_LOG):
            _all_rows: list[dict] = []
            with open(config.DETECTIVE_LOG, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _d = _json.loads(_line)
                        _all_rows.append({
                            "date":     _d.get("ts", "")[:10],
                            "severity": _d.get("severity", "info"),
                            "inspector": _d.get("inspector", ""),
                        })
                    except _json.JSONDecodeError:
                        continue

            if _all_rows:
                _hist_df = pd.DataFrame(_all_rows)
                _trend = (
                    _hist_df.groupby(["date", "severity"])
                    .size()
                    .reset_index(name="count")
                )
                _fig_trend = px.bar(
                    _trend,
                    x="date", y="count", color="severity",
                    title="Detective Findings by Day and Severity",
                    color_discrete_map={
                        "critical": "#e74c3c",
                        "high":     "#e67e22",
                        "medium":   "#f1c40f",
                        "low":      "#3498db",
                        "info":     "#95a5a6",
                    },
                    barmode="stack",
                )
                st.plotly_chart(_fig_trend, use_container_width=True)
            else:
                st.info("Not enough history to plot a trend yet.")
        else:
            st.info("No detective log found.")