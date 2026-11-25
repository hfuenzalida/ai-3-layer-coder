"""
Codex Futura — Live Monitor
Floating dark window showing layer health and real-time streaming output.
Run standalone: python monitor.py
Auto-start setup: python setup_autostart.py
"""
import tkinter as tk
from tkinter import font as tkfont
import threading
import time
import json
import os
import sys
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# ── Palette ───────────────────────────────────────────────────────
BG      = "#0d1117"
BG2     = "#161b22"
BG3     = "#21262d"
GREEN   = "#3fb950"
YELLOW  = "#d29922"
RED     = "#f85149"
BLUE    = "#58a6ff"
CYAN    = "#79c0ff"
TEXT    = "#e6edf3"
DIM     = "#8b949e"
ACCENT  = "#1f6feb"
PURPLE  = "#bc8cff"

TIER_COLORS = {
    "Light":        GREEN,
    "Heavy":        YELLOW,
    "Orchestrator": BLUE,
    "Unknown":      DIM,
}

LAYER_DEFS = [
    ("Layer 1 — Local (Ollama)",    "local"),
    ("Layer 2 — Cloud (Qwen 32B)",  "cloud"),
    ("Layer 3 — Orchestrator",      "orchestrator"),
]


class CodexMonitor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._drag_x = 0
        self._drag_y = 0
        self._setup_window()
        self._build_ui()
        self._start_threads()

    # ── Window setup ──────────────────────────────────────────────

    def _setup_window(self):
        self.root.title("Codex Futura Monitor")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)

        w, h = 500, 580
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - w - 24
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(380, 400)
        self.root.resizable(True, True)

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_motion(self, event):
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._on_restore)

    def _on_restore(self, event):
        self.root.overrideredirect(True)
        self.root.unbind("<Map>")
        self.root.attributes("-topmost", True)

    def _toggle_topmost(self):
        current = self.root.attributes("-topmost")
        self.root.attributes("-topmost", not current)
        self.pin_btn.configure(fg=CYAN if not current else DIM)

    # ── UI construction ───────────────────────────────────────────

    def _lbl(self, parent, text, fg=TEXT, size=9, bold=False, mono=False, **kw):
        family = "Consolas" if mono else "Segoe UI"
        weight = "bold" if bold else "normal"
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=(family, size, weight), **kw)

    def _build_ui(self):
        self._build_titlebar()
        self._build_layers()
        tk.Frame(self.root, bg=ACCENT, height=1).pack(fill=tk.X)
        self._build_feed()
        self._build_footer()

    def _build_titlebar(self):
        bar = tk.Frame(self.root, bg=BG2, height=38)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        bar.bind("<ButtonPress-1>", self._drag_start)
        bar.bind("<B1-Motion>",     self._drag_motion)

        self._lbl(bar, "◉  CODEX FUTURA", fg=CYAN, size=10, bold=True).pack(side=tk.LEFT, padx=12)
        self._lbl(bar, "MONITOR",          fg=DIM,  size=9).pack(side=tk.LEFT)

        tk.Button(bar, text="✕", bg=BG2, fg=DIM, relief="flat", bd=0,
                  font=("Segoe UI", 12), cursor="hand2", activebackground=BG2, activeforeground=RED,
                  command=self.root.destroy).pack(side=tk.RIGHT, padx=(4, 10))
        tk.Button(bar, text="─", bg=BG2, fg=DIM, relief="flat", bd=0,
                  font=("Segoe UI", 12), cursor="hand2", activebackground=BG2, activeforeground=TEXT,
                  command=self._minimize).pack(side=tk.RIGHT, padx=2)
        self.pin_btn = tk.Button(bar, text="📌", bg=BG2, fg=CYAN, relief="flat", bd=0,
                                 font=("Segoe UI", 10), cursor="hand2", activebackground=BG2,
                                 command=self._toggle_topmost)
        self.pin_btn.pack(side=tk.RIGHT, padx=2)

    def _build_layers(self):
        frame = tk.Frame(self.root, bg=BG2)
        frame.pack(fill=tk.X)

        self._lbl(frame, "  LAYERS", fg=DIM, size=8, bold=True).pack(anchor="w", padx=10, pady=(6, 2))

        self.layer_dots   = []
        self.layer_status = []

        for name, _ in LAYER_DEFS:
            row = tk.Frame(frame, bg=BG2)
            row.pack(fill=tk.X, padx=10, pady=1)

            dot = tk.Label(row, text="●", bg=BG2, fg=DIM, font=("Segoe UI", 13))
            dot.pack(side=tk.LEFT)
            tk.Label(row, text=f"  {name}", bg=BG2, fg=TEXT,
                     font=("Segoe UI", 9)).pack(side=tk.LEFT)
            st = tk.Label(row, text="checking...", bg=BG2, fg=DIM,
                          font=("Segoe UI", 8))
            st.pack(side=tk.RIGHT, padx=6)

            self.layer_dots.append(dot)
            self.layer_status.append(st)

        # Budget row
        self.budget_lbl = self._lbl(frame, "  Budget: checking...", fg=DIM, size=8)
        self.budget_lbl.pack(anchor="w", padx=10, pady=(4, 8))

    def _build_feed(self):
        # Header row
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill=tk.X)

        self._lbl(hdr, "  LIVE ACTIVITY", fg=DIM, size=8, bold=True).pack(side=tk.LEFT,  padx=10, pady=(8, 4))
        self.tier_badge = tk.Label(hdr, text="IDLE", bg=BG, fg=DIM,
                                   font=("Segoe UI", 8, "bold"))
        self.tier_badge.pack(side=tk.RIGHT, padx=10, pady=(8, 4))

        # Task + model info
        self.task_lbl = tk.Label(self.root, text="  Waiting for tasks...", bg=BG, fg=DIM,
                                 font=("Segoe UI", 9), anchor="w")
        self.task_lbl.pack(fill=tk.X, padx=4)

        self.model_lbl = tk.Label(self.root, text="", bg=BG, fg=DIM,
                                  font=("Consolas", 8), anchor="w")
        self.model_lbl.pack(fill=tk.X, padx=4, pady=(0, 4))

        tk.Frame(self.root, bg=BG3, height=1).pack(fill=tk.X)

        # Scrollable code area
        code_wrap = tk.Frame(self.root, bg=BG3)
        code_wrap.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.code_text = tk.Text(
            code_wrap,
            bg=BG3, fg=TEXT,
            font=("Consolas", 10),
            relief="flat", bd=0,
            wrap=tk.WORD,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            padx=10, pady=8,
            state=tk.DISABLED,
            cursor="arrow",
        )
        sb = tk.Scrollbar(code_wrap, command=self.code_text.yview,
                          bg=BG3, troughcolor=BG3, relief="flat", bd=0, width=8)
        self.code_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.code_text.pack(fill=tk.BOTH, expand=True)

    def _build_footer(self):
        tk.Frame(self.root, bg=BG3, height=1).pack(fill=tk.X)
        foot = tk.Frame(self.root, bg=BG2, height=28)
        foot.pack(fill=tk.X)
        foot.pack_propagate(False)

        self.footer_lbl = tk.Label(foot, text="Session: — tokens · $— · — tasks",
                                   bg=BG2, fg=DIM, font=("Segoe UI", 8))
        self.footer_lbl.pack(side=tk.LEFT, padx=10)

        self.clock_lbl = tk.Label(foot, text="", bg=BG2, fg=DIM,
                                  font=("Segoe UI", 8))
        self.clock_lbl.pack(side=tk.RIGHT, padx=10)

    # ── Background threads ────────────────────────────────────────

    def _start_threads(self):
        # Probe immediately so dots are populated within seconds, not after 30s
        threading.Thread(target=self._run_health_check, daemon=True).start()
        threading.Thread(target=self._health_loop,      daemon=True).start()
        threading.Thread(target=self._feed_loop,        daemon=True).start()
        self._tick_clock()
        self._update_session_footer()

    def _tick_clock(self):
        self.clock_lbl.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    # ── Health check ──────────────────────────────────────────────

    def _health_loop(self):
        while True:
            self._run_health_check()
            time.sleep(30)

    def _run_health_check(self):
        # Layer 1 — Ollama
        try:
            r = requests.post(
                config.LOCAL_API_URL,
                json={"model": config.LOCAL_MODEL_NAME,
                      "messages": [{"role": "user", "content": "ping"}]},
                timeout=5,
            )
            ok = r.status_code == 200
            self.root.after(0, self._set_layer, 0, ok, "Online" if ok else f"HTTP {r.status_code}")
        except requests.exceptions.ConnectionError:
            self.root.after(0, self._set_layer, 0, False, "Offline")
        except Exception as e:
            self.root.after(0, self._set_layer, 0, False, str(e)[:22])

        # Layer 2 — OpenRouter
        try:
            r = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                timeout=5,
            )
            ok = r.status_code == 200
            self.root.after(0, self._set_layer, 1, ok, "Connected" if ok else f"HTTP {r.status_code}")
        except Exception:
            self.root.after(0, self._set_layer, 1, False, "Unreachable")

        # Layer 3 — always active when cloud is up (no separate endpoint)
        self.root.after(0, self._set_layer, 2, True, "Active")

        # Budget
        hard = os.path.exists(config.LOCK_FILE)
        soft = os.path.exists(config.SOFT_LOCK_FILE)
        if hard:
            txt, col = "  Budget: HARD LOCKED — all cloud blocked", RED
        elif soft:
            txt, col = "  Budget: SOFT LOCKED (80%+) — Orchestrator blocked", YELLOW
        else:
            txt, col = "  Budget: OK", GREEN
        self.root.after(0, lambda t=txt, c=col: self.budget_lbl.configure(text=t, fg=c))

    def _set_layer(self, idx: int, online: bool, msg: str):
        self.layer_dots[idx].configure(fg=GREEN if online else RED)
        self.layer_status[idx].configure(text=msg, fg=GREEN if online else RED)

    # ── Live feed ─────────────────────────────────────────────────

    def _feed_loop(self):
        last_content = None
        last_mtime   = 0.0
        while True:
            try:
                if os.path.exists(config.MONITOR_CURRENT):
                    mtime = os.stat(config.MONITOR_CURRENT).st_mtime
                    if mtime != last_mtime:               # only parse when file changed
                        with open(config.MONITOR_CURRENT, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        # Only advance the mtime cursor after a successful parse
                        last_mtime = mtime
                        if data.get("idle"):
                            if last_content is not None:
                                last_content = None
                                self.root.after(0, self._show_idle)
                        else:
                            content = data.get("content", "")
                            if content != last_content:
                                last_content = content
                                self.root.after(0, self._show_active,
                                                data.get("task", ""),
                                                data.get("model", ""),
                                                data.get("tier", ""),
                                                content)
            except Exception:
                pass
            time.sleep(0.3)

    def _show_idle(self):
        self.task_lbl.configure(text="  Waiting for tasks...", fg=DIM)
        self.model_lbl.configure(text="")
        self.tier_badge.configure(text="IDLE", fg=DIM)
        self._set_code("")

    def _show_active(self, task: str, model: str, tier: str, content: str):
        col = TIER_COLORS.get(tier, DIM)
        self.task_lbl.configure(text=f"  {task}", fg=TEXT)
        self.model_lbl.configure(text=f"  {model}", fg=DIM)
        self.tier_badge.configure(text=tier.upper() if tier else "ACTIVE", fg=col)
        self._set_code(content)

    def _set_code(self, text: str):
        self.code_text.configure(state=tk.NORMAL)
        self.code_text.delete("1.0", tk.END)
        if text:
            self.code_text.insert(tk.END, text)
            self.code_text.see(tk.END)
        self.code_text.configure(state=tk.DISABLED)

    # ── Session footer ────────────────────────────────────────────

    _footer_mtime: float = 0.0   # class-level mtime cache — skip read if file unchanged

    def _update_session_footer(self):
        """
        Read current_session.json (written by the REPL after every task) to show
        live session totals.  Falls back to a placeholder when no session is active.
        Uses mtime gating to avoid unnecessary file reads.
        """
        try:
            if os.path.exists(config.SESSION_FILE):
                mtime = os.stat(config.SESSION_FILE).st_mtime
                if mtime != self._footer_mtime:
                    with open(config.SESSION_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._footer_mtime = mtime
                    tokens = data.get("tokens", 0)
                    cost   = data.get("cost",   0.0)
                    tasks  = data.get("tasks",  0)
                    sid    = data.get("session_id", "—")
                    self.footer_lbl.configure(
                        text=f"Session {sid}: {tokens:,} tokens · ${cost:.4f} · {tasks} tasks"
                    )
        except Exception:
            pass
        self.root.after(10_000, self._update_session_footer)  # reschedule every 10s


def main():
    os.makedirs(config.BASE_DIR, exist_ok=True)
    root = tk.Tk()
    CodexMonitor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
