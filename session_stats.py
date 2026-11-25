"""
Session-level token tracking and cost calculation.
Maintains in-memory stats during a session and logs to CSV.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
import config


@dataclass
class TaskStats:
    """Per-task statistics."""
    task_id: int
    task_summary: str
    model: str
    tier: str
    tokens: int
    cost_usd: float
    timestamp: str
    routing_reason: str = ""
    provider: str = ""
    status: str = "Success"


@dataclass
class SessionStats:
    """In-memory session statistics."""
    session_id: str
    start_time: str
    tasks: list[TaskStats] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(t.tokens for t in self.tasks)

    @property
    def total_cost(self) -> float:
        return sum(t.cost_usd for t in self.tasks)

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def by_model(self) -> dict[str, dict]:
        """Breakdown by model."""
        breakdown = {}
        for task in self.tasks:
            if task.model not in breakdown:
                breakdown[task.model] = {"tokens": 0, "cost": 0.0, "count": 0}
            breakdown[task.model]["tokens"] += task.tokens
            breakdown[task.model]["cost"] += task.cost_usd
            breakdown[task.model]["count"] += 1
        return breakdown

    @property
    def by_tier(self) -> dict[str, dict]:
        """Breakdown by tier (Light/Heavy/Orchestrator)."""
        breakdown = {}
        for task in self.tasks:
            if task.tier not in breakdown:
                breakdown[task.tier] = {"tokens": 0, "cost": 0.0, "count": 0}
            breakdown[task.tier]["tokens"] += task.tokens
            breakdown[task.tier]["cost"] += task.cost_usd
            breakdown[task.tier]["count"] += 1
        return breakdown

    @property
    def by_provider(self) -> dict[str, dict]:
        """Breakdown by provider (Local, Cloud)."""
        breakdown = {}
        for task in self.tasks:
            provider = task.provider or infer_provider(task.model)
            if provider not in breakdown:
                breakdown[provider] = {"tokens": 0, "cost": 0.0, "count": 0}
            breakdown[provider]["tokens"] += task.tokens
            breakdown[provider]["cost"] += task.cost_usd
            breakdown[provider]["count"] += 1
        return breakdown


def infer_provider(model: str) -> str:
    """
    Infer a human-readable provider from a model name.
    OpenRouter models always contain a forward-slash (e.g. 'qwen/qwen-2.5-coder-32b-instruct').
    Local Ollama models never do (e.g. 'qwen2.5-coder:3b').
    """
    m = model.lower()

    # Local Ollama model — no provider prefix
    if "/" not in model:
        return "Local (Ollama)"

    # Cloud models — keyed by provider prefix or keyword
    if "anthropic" in m or "claude" in m:
        return "Cloud (Anthropic)"
    if "deepseek" in m:
        return "Cloud (DeepSeek)"
    if "openai" in m or "gpt" in m:
        return "Cloud (OpenAI)"
    if "qwen" in m:
        if ":free" in m:
            return "Cloud (Qwen Free)"
        return "Cloud (Qwen Paid)"

    return "Cloud (Unknown)"


def calculate_cost(model: str, tokens: int) -> float:
    """Calculate cost in USD using the canonical pricing table in config.MODEL_PRICING."""
    pricing = config.MODEL_PRICING

    # Exact match first
    if model in pricing:
        return (tokens / 1_000_000) * pricing[model]

    # Partial match (handles vendor-prefixed names like "anthropic/claude-3.5-sonnet")
    for key, price in pricing.items():
        if key in model:
            return (tokens / 1_000_000) * price

    # Conservative fallback for unknown models
    return (tokens / 1_000_000) * config.FALLBACK_COST_PER_1M


def log_session_stats_to_csv(session: SessionStats):
    """Append session summary to a session log CSV."""
    os.makedirs(config.BASE_DIR, exist_ok=True)
    session_log = os.path.join(config.BASE_DIR, "session_log.csv")

    # Create CSV header if needed
    if not os.path.exists(session_log):
        with open(session_log, "w", encoding="utf-8") as f:
            f.write("session_id,start_time,end_time,task_count,total_tokens,total_cost_usd,top_model,top_tier\n")

    # Find top model and tier
    by_model = session.by_model
    by_tier = session.by_tier
    top_model = max(by_model.items(), key=lambda x: x[1]["cost"])[0] if by_model else "N/A"
    top_tier = max(by_tier.items(), key=lambda x: x[1]["cost"])[0] if by_tier else "N/A"

    end_time = datetime.now().isoformat()
    with open(session_log, "a", encoding="utf-8") as f:
        f.write(
            f"{session.session_id},{session.start_time},{end_time},"
            f"{session.task_count},{session.total_tokens},{session.total_cost:.4f},"
            f"{top_model},{top_tier}\n"
        )


def export_session_json(session: SessionStats, output_path: str = None):
    """Export full session data as JSON."""
    if output_path is None:
        output_dir = os.path.join(config.BASE_DIR, "sessions")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{session.session_id}.json")

    data = {
        "session_id": session.session_id,
        "start_time": session.start_time,
        "end_time": datetime.now().isoformat(),
        "summary": {
            "task_count": session.task_count,
            "total_tokens": session.total_tokens,
            "total_cost_usd": session.total_cost,
        },
        "by_model": session.by_model,
        "by_tier": session.by_tier,
        "by_provider": session.by_provider,
        "tasks": [asdict(t) for t in session.tasks],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return output_path
