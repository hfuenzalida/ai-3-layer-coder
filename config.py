import os
from dotenv import load_dotenv
from __version__ import __version__  # noqa: F401 — re-exported for easy import

load_dotenv()

BASE_DIR = os.path.expanduser("~/codex_futura/analytics")
LOG_FILE = os.path.join(BASE_DIR, "model_usage_2026.csv")
ARCHIVE_DIR     = os.path.join(BASE_DIR, "archives")
LAST_RESET_FILE = os.path.join(BASE_DIR, "last_reset.txt")
LOCK_FILE = os.path.join(BASE_DIR, "budget.lock")
SOFT_LOCK_FILE = os.path.join(BASE_DIR, "budget_soft.lock")
MONITOR_CURRENT = os.path.join(BASE_DIR, "monitor_current.json")
# Written by the REPL after every task so the floating monitor shows
# live current-session totals rather than the cumulative monthly log.
SESSION_FILE    = os.path.join(BASE_DIR, "current_session.json")
DETECTIVE_LOG   = os.path.join(BASE_DIR, "detective_log.jsonl")

# Prompt templates
TEMPLATES_DIR = os.path.join(os.path.expanduser("~"), "codex_futura", "templates")

# Session context memory — how many past task summaries to inject into cloud prompts
CONTEXT_WINDOW_SIZE = 3

# Pre-flight cost warning — show estimate + confirm for Orchestrator calls above this (USD)
PRE_FLIGHT_USD_THRESHOLD = 0.01

# Allowed output directory for generated files (task_executor._save_to_disk)
# Override via OUTPUT_DIR env var. Files written outside this tree are rejected.
OUTPUT_DIR = os.path.realpath(os.getenv("OUTPUT_DIR", os.getcwd()))

# Timeout for a single local (Ollama) streaming request.
# 120s gives a 3B model on modest hardware room to finish a 300-line function.
LOCAL_STREAM_TIMEOUT = int(os.getenv("LOCAL_STREAM_TIMEOUT", "120"))

# Per-task wall-clock budget for the `batch` command.
# If a single task exceeds this, it is logged as "Timed out" and skipped.
BATCH_TASK_TIMEOUT_SEC = int(os.getenv("BATCH_TASK_TIMEOUT_SEC", "300"))

# Maximum USD spend allowed for a single `plan` workflow run.
# If cumulative task cost crosses this threshold mid-workflow, execution
# stops and remaining tasks are skipped.  Override via WORKFLOW_MAX_COST_USD.
WORKFLOW_MAX_COST_USD = float(os.getenv("WORKFLOW_MAX_COST_USD", "5.00"))

# Exchange rate and Budget Settings
USD_TO_CLP = 928.39
EXCHANGE_RATE = USD_TO_CLP
BUDGET_LIMIT_CLP = 50000
BUDGET_SOFT_THRESHOLD = 0.80  # 80% triggers soft lock (Orchestrator tier blocked)

# Model Pricing — single source of truth (USD per 1M tokens)
# Both session_stats.py and codex_logger.py must use this table.
MODEL_PRICING = {
    "qwen/qwen-2.5-coder-32b-instruct:free": 0.0,
    "qwen/qwen-2.5-coder-32b-instruct":      0.07,
    "qwen/qwen3-coder:free":                  0.0,
    "qwen/qwen3-coder":                       0.14,
    "deepseek/deepseek-chat-v3-0324":         0.27,
    "anthropic/claude-3.5-sonnet":            15.0,
    "openai/gpt-4o":                          5.0,
}
FALLBACK_COST_PER_1M = 15.0  # Conservative fallback for unknown models
CLAUDE_AVG_COST_PER_1K = 0.015  # Kept for backward compatibility

# API Keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Local Model Settings (Ollama)
LOCAL_API_URL = os.getenv("LOCAL_API_URL", "http://localhost:11434/v1/chat/completions")
LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "qwen2.5-coder:3b")
