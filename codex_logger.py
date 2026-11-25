import math
import os
import csv
import threading
from datetime import datetime
from mcp.server.fastmcp import FastMCP
import config
from session_stats import calculate_cost, infer_provider

# Hard cap on manually-entered cost values (USD). Prevents MCP clients from
# writing absurd numbers to the analytics CSV via the cost_override parameter.
_MAX_COST_OVERRIDE = 100.0

# Initialize the MCP server
mcp = FastMCP("CodexLogger")

os.makedirs(config.BASE_DIR, exist_ok=True)

HEADER = ["timestamp", "model", "provider", "tier", "tokens", "estimated_cost", "status", "task_summary"]

# Thread-safe lock for CSV writes (prevents corruption under concurrent tasks)
_log_lock = threading.Lock()

if not os.path.exists(config.LOG_FILE):
    with open(config.LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)


def log_task(model: str, tier: str, tokens: int, task: str, status: str = "Success", cost_override: float = None) -> str:
    """
    Core logging logic. Thread-safe via _log_lock.
    Uses config.MODEL_PRICING via calculate_cost() — single source of truth.
    cost_override is validated: must be a finite, non-negative number ≤ $100.
    """
    if cost_override is not None:
        if not isinstance(cost_override, (int, float)) or math.isnan(cost_override) or math.isinf(cost_override):
            return "Logging Error: cost_override must be a finite number."
        if cost_override < 0:
            return "Logging Error: cost_override cannot be negative."
        if cost_override > _MAX_COST_OVERRIDE:
            return f"Logging Error: cost_override ${cost_override:.2f} exceeds the ${_MAX_COST_OVERRIDE:.0f} cap."

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        provider  = infer_provider(model)
        cost = float(cost_override) if cost_override is not None else (
            0.0 if tier == "Light" else calculate_cost(model, tokens)
        )

        with _log_lock:
            file_exists = os.path.exists(config.LOG_FILE)
            with open(config.LOG_FILE, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(HEADER)
                writer.writerow([timestamp, model, provider, tier, tokens, f"${cost:.4f}", status, task])

        return f"Logged {tier} task to Codex Analytics. Provider: {provider}."
    except Exception as e:
        return f"Logging Error: {str(e)}"


@mcp.tool()
def record_usage(model: str, tier: str, tokens: int, task: str, status: str = "Success", cost_override: float = None) -> str:
    """
    Records usage across the Triple-Layer setup.
    Args:
        model: Model name (e.g., 'qwen2.5-coder:3b', 'qwen/qwen-2.5-coder-32b-instruct')
        tier: 'Orchestrator', 'Heavy', or 'Light'
        tokens: Estimated tokens
        task: Brief description
        status: 'Success', 'Fallback', or 'Cache'
        cost_override: Manual cost entry (USD) — overrides automatic calculation
    """
    return log_task(model, tier, tokens, task, status, cost_override)


if __name__ == "__main__":
    mcp.run()
