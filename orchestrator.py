"""
Codex Futura — Orchestrator
-----------------------------
Central routing and execution engine for the 3-layer AI ecosystem.

Responsibilities deliberately kept separate:
  CodexRouter  — pure routing decision (no I/O, no state)
  Orchestrator — execution, circuit breaking, caching, logging
  execute_task — thin coordinator that delegates to private step-methods
"""
import json
import os
import queue
import re
import threading
import time

import requests
import requests.adapters

import cache as response_cache
import config
from codex_logger import log_task
from session_stats import infer_provider

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule

console = Console()


class StreamTruncatedError(Exception):
    """
    Raised when an SSE stream ends without the expected [DONE] sentinel,
    indicating a dropped connection or a server-side truncation mid-response.
    Carries whatever partial content was received so callers can decide
    whether to surface it or discard it.
    """
    def __init__(self, partial_content: str, tokens: int) -> None:
        self.partial_content = partial_content
        self.tokens          = tokens
        super().__init__(
            f"Stream truncated after ~{tokens} tokens "
            f"({len(partial_content):,} chars received, no [DONE] sentinel)."
        )


# ── Circuit Breaker — module-level, shared across Orchestrator instances ───────

_local_failure_count    = 0
_local_circuit_open     = False
_local_circuit_open_until = 0.0
_LOCAL_FAILURE_THRESHOLD  = 3
_LOCAL_CIRCUIT_COOLDOWN   = 300   # seconds before half-open retry

_CIRCUIT_STATE_FILE = os.path.join(config.BASE_DIR, "circuit_breaker.json")


def _load_circuit_state() -> None:
    global _local_failure_count, _local_circuit_open, _local_circuit_open_until
    try:
        with open(_CIRCUIT_STATE_FILE) as f:
            state = json.load(f)
        _local_failure_count      = state.get("failure_count",      0)
        _local_circuit_open       = state.get("circuit_open",       False)
        _local_circuit_open_until = state.get("circuit_open_until", 0.0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_circuit_state() -> None:
    try:
        with open(_CIRCUIT_STATE_FILE, "w") as f:
            json.dump({
                "failure_count":      _local_failure_count,
                "circuit_open":       _local_circuit_open,
                "circuit_open_until": _local_circuit_open_until,
            }, f)
    except Exception:
        pass


_load_circuit_state()


# ── Monitor file helpers ───────────────────────────────────────────────────────

_MONITOR_WRITE_GATE   = 0.20        # minimum seconds between mid-stream writes
_last_monitor_write_t = 0.0         # module-level timestamp of last write


def _monitor_write(task: str, model: str, tier: str, content: str, *, force: bool = False) -> None:
    """
    Write streaming progress to the monitor file.
    Mid-stream calls are time-gated to at most one write per 200 ms.
    Pass force=True for the final post-loop write to guarantee it always lands.
    """
    global _last_monitor_write_t
    now = time.monotonic()
    if not force and (now - _last_monitor_write_t) < _MONITOR_WRITE_GATE:
        return
    try:
        os.makedirs(os.path.dirname(config.MONITOR_CURRENT), exist_ok=True)
        with open(config.MONITOR_CURRENT, "w", encoding="utf-8") as f:
            json.dump(
                {"idle": False, "task": task, "model": model, "tier": tier, "content": content},
                f, ensure_ascii=False,
            )
        _last_monitor_write_t = now
    except Exception:
        pass


def _monitor_idle() -> None:
    try:
        with open(config.MONITOR_CURRENT, "w", encoding="utf-8") as f:
            json.dump({"idle": True}, f)
    except Exception:
        pass


# ── Tier display colours ───────────────────────────────────────────────────────

TIER_COLORS = {
    "Light":        "green",
    "Heavy":        "yellow",
    "Orchestrator": "blue",
}


# ── SSE streaming renderer ─────────────────────────────────────────────────────

def _stream_response(response, model_label: str, tier: str, task_summary: str) -> tuple[str, int]:
    """
    Consume an SSE streaming response, display it in a live Rich panel,
    and return (full_content, total_tokens).

    Network I/O runs in a daemon thread so the main thread stays responsive
    to Ctrl+C. Parsed SSE events are passed to the main thread via a Queue.
    A sentinel value of None signals end-of-stream.
    """
    _QUEUE_DRAIN_INTERVAL = 0.05   # seconds to sleep between empty-queue checks

    color      = TIER_COLORS.get(tier, "white")
    short_task = (task_summary[:67] + "...") if len(task_summary) > 70 else task_summary
    _monitor_write(task_summary, model_label, tier, "")

    sse_queue: queue.Queue = queue.Queue()

    def _reader() -> None:
        """Daemon thread: read iter_lines(), parse SSE, enqueue events."""
        try:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    sse_queue.put({"_done": True})
                    return
                try:
                    sse_queue.put(json.loads(data_str))
                except json.JSONDecodeError:
                    continue
        finally:
            sse_queue.put(None)   # sentinel: stream ended (with or without [DONE])

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    chunks: list[str] = []
    token_count   = 0
    done_received = False

    with Live(console=console, refresh_per_second=15) as live:
        while True:
            try:
                event = sse_queue.get(timeout=_QUEUE_DRAIN_INTERVAL)
            except queue.Empty:
                continue

            if event is None:
                # sentinel — reader thread exited
                break

            if event.get("_done"):
                done_received = True
                # drain any remaining items before breaking
                while True:
                    try:
                        sse_queue.get_nowait()
                    except queue.Empty:
                        break
                break

            delta = event.get("choices", [{}])[0].get("delta", {})
            piece = delta.get("content") or ""
            if piece:
                chunks.append(piece)
                _monitor_write(task_summary, model_label, tier, "".join(chunks))
            usage = event.get("usage")
            if usage:
                token_count = usage.get("total_tokens", token_count)
            elif piece:
                token_count += len(piece.split())

            live.update(Panel(
                "".join(chunks) or "[dim]Waiting for first token...[/dim]",
                title=f"[bold {color}]{tier} — {model_label}[/bold {color}]",
                subtitle=f"[dim]{short_task}[/dim]   [cyan]~{token_count} tokens[/cyan]",
                border_style=color,
                padding=(0, 1),
            ))

    reader_thread.join(timeout=2)

    full_content = "".join(chunks)
    _monitor_write(task_summary, model_label, tier, full_content, force=True)
    console.print()

    # Guard: if the server closed the connection without [DONE], the response
    # is incomplete. Raise so callers can handle appropriately rather than
    # silently returning a potentially broken partial result.
    if not done_received and full_content:
        raise StreamTruncatedError(full_content, token_count)

    return full_content, token_count


# ── Router ─────────────────────────────────────────────────────────────────────

class CodexRouter:
    """
    Pure routing decision — no I/O, no mutable state.
    Keyword patterns are class-level constants compiled once at import time.
    Word-boundary anchors (\b) prevent false matches (e.g. "import" inside
    "important') and are faster than plain substring search on long prompts.
    """

    # Each entry: (compiled_pattern, weight)
    _ORCHESTRATOR_KW: list[tuple[re.Pattern, int]] = [
        (re.compile(r'\barchitecture\b'),    3),
        (re.compile(r'\bdesign\s+pattern\b'),3),
        (re.compile(r'\bsecurity\s+audit\b'),3),
        (re.compile(r'\bstrategy\b'),        2),
        (re.compile(r'\bventure\s+capital\b'),2),
        (re.compile(r'\breview\b'),          2),
        (re.compile(r'\blogic\s+fix\b'),     2),
        (re.compile(r'\btrade-off\b'),       2),
        (re.compile(r'\bevaluate\b'),        1),
    ]
    _HEAVY_KW: list[tuple[re.Pattern, int]] = [
        (re.compile(r'\brefactor\b'),        2),
        (re.compile(r'\bunit\s+test\b'),     2),
        (re.compile(r'\bdata\s+parsing\b'),  2),
        (re.compile(r'\bimplement\b'),       1),
        (re.compile(r'\bwrite\s+file\b'),    1),
        (re.compile(r'\bmigration\b'),       2),
        (re.compile(r'\bcss\b'),             1),
        (re.compile(r'\bhtml\b'),            1),
        (re.compile(r'\bapi\s+endpoint\b'),  2),
    ]
    _LOCAL_KW: list[tuple[re.Pattern, int]] = [
        (re.compile(r'\bformat\b'),          2),
        (re.compile(r'\bdocstrings?\b'),      2),
        (re.compile(r'\bboilerplate\b'),     2),
        (re.compile(r'\blint\b'),            2),
        (re.compile(r'\bregex\b'),           1),
        (re.compile(r'\bsimple\b'),          1),
        (re.compile(r'\brename\b'),          1),
        (re.compile(r'\bcomment\b'),         1),
        (re.compile(r'\btype\s+hints?\b'),    1),
        (re.compile(r'\bimport\b'),          1),
    ]

    def route_request(self, user_prompt: str) -> tuple[str, str, str]:
        prompt_lower = user_prompt.lower()
        word_count   = len(user_prompt.split())

        local_score  = sum(w for p, w in self._LOCAL_KW        if p.search(prompt_lower))
        heavy_score  = sum(w for p, w in self._HEAVY_KW        if p.search(prompt_lower))
        claude_score = sum(w for p, w in self._ORCHESTRATOR_KW if p.search(prompt_lower))

        # Long prompts favour cloud tiers
        if word_count > 200:
            local_score  = max(0, local_score - 3)
            heavy_score += 2
        elif word_count > 150:
            local_score  = max(0, local_score - 1)

        scores    = {"local": local_score, "coding": heavy_score, "strategic": claude_score}
        best_duty = max(scores, key=scores.get)

        if scores[best_duty] == 0:
            return "coding", "QWEN_32B", "No keyword match — defaulting to Heavy (safe fallback)."

        targets = {"local": "QWEN2.5-CODER:3B", "coding": "QWEN2.5-CODER-32B", "strategic": "CLAUDE_SONNET"}
        reasons = {
            "local":     "Lightweight routine task suited for local.",
            "coding":    "Heavy-duty coding / high-volume generation.",
            "strategic": "High-level reasoning required.",
        }
        score_str = f"scores(local={local_score}, heavy={heavy_score}, claude={claude_score})"
        return best_duty, targets[best_duty], f"{reasons[best_duty]} [{score_str}]"


# ── Orchestrator ───────────────────────────────────────────────────────────────

class Orchestrator:

    def __init__(self) -> None:
        self.api_key    = config.OPENROUTER_API_KEY
        self.url        = "https://openrouter.ai/api/v1/chat/completions"
        self.local_url  = config.LOCAL_API_URL
        self.local_model = config.LOCAL_MODEL_NAME
        self.router     = CodexRouter()
        self._context: list[str] = []   # rolling task-summary memory for cloud prompts

        # Reuse TCP connections across all requests in this session
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=8,
            max_retries=requests.adapters.Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist={429, 502, 503, 504},
                allowed_methods={"POST", "GET"},
            ),
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://",  adapter)

    # ── Circuit breaker ────────────────────────────────────────────────────────

    def _is_local_circuit_open(self) -> bool:
        global _local_circuit_open, _local_circuit_open_until, _local_failure_count
        if _local_circuit_open:
            if time.time() > _local_circuit_open_until:
                _local_circuit_open   = False
                _local_failure_count  = 0
                _save_circuit_state()
                console.print("[yellow]⚡ Circuit breaker: cooldown passed, probing local.[/yellow]")
                return False
            remaining = int(_local_circuit_open_until - time.time())
            console.print(f"[red]⛔ Circuit breaker OPEN: local disabled for {remaining}s more.[/red]")
            return True
        return False

    def _record_local_failure(self) -> None:
        global _local_failure_count, _local_circuit_open, _local_circuit_open_until
        _local_failure_count += 1
        if _local_failure_count >= _LOCAL_FAILURE_THRESHOLD:
            _local_circuit_open       = True
            _local_circuit_open_until = time.time() + _LOCAL_CIRCUIT_COOLDOWN
            console.print(
                f"[bold red]🚨 Circuit breaker OPEN after {_LOCAL_FAILURE_THRESHOLD} failures. "
                f"Local paused for {_LOCAL_CIRCUIT_COOLDOWN // 60} min. Check Ollama.[/bold red]"
            )
        _save_circuit_state()

    def _record_local_success(self) -> None:
        global _local_failure_count, _local_circuit_open
        _local_failure_count = 0
        _local_circuit_open  = False
        _save_circuit_state()

    # ── Low-level streaming callers ────────────────────────────────────────────

    def _run_local_stream(self, prompt: str, task_summary: str) -> tuple[str, int]:
        """Call Ollama with streaming. Returns (content, tokens)."""
        response = self._session.post(
            self.local_url,
            json={"model": self.local_model, "messages": [{"role": "user", "content": prompt}], "stream": True},
            timeout=config.LOCAL_STREAM_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()
        return _stream_response(response, self.local_model, "Light", task_summary)

    def _build_messages(self, prompt: str) -> list:
        """Wrap prompt with rolling session context."""
        msgs: list[dict] = []
        if self._context:
            recent = self._context[-config.CONTEXT_WINDOW_SIZE:]
            ctx_text = (
                "Recent session context "
                "(use this if the user references 'that', 'it', 'the previous', etc.):\n"
                + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(recent))
            )
            msgs.append({"role": "system", "content": ctx_text})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _run_cloud_stream(
        self, messages: list, model_chain: list, tier: str, task_summary: str
    ) -> tuple[str, str, int]:
        """Call OpenRouter with streaming. Returns (content, used_model, tokens)."""
        response = self._session.post(
            url=self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model":    model_chain[0],
                "models":   model_chain,
                "messages": messages,
                "route":    "fallback",
                "stream":   True,
            },
            stream=True,
        )
        response.raise_for_status()
        used_model = response.headers.get("x-openrouter-model", model_chain[0])
        label      = used_model.split("/")[-1]
        content, tokens = _stream_response(response, label, tier, task_summary)
        return content, used_model, tokens

    # ── execute_task step-methods ──────────────────────────────────────────────

    def _budget_gate(self, duty: str) -> tuple[str | None, str]:
        """
        Check budget locks.
        Returns (error_msg_or_None, effective_duty).
        effective_duty may be downgraded from 'strategic' → 'coding' by a soft lock.
        """
        if duty == "local":
            return None, duty
        if os.path.exists(config.LOCK_FILE):
            console.print("[bold red]🚨 HARD LOCK: Budget exceeded. All cloud blocked.[/bold red]")
            return (
                "SYSTEM LOCKED: Cloud budget exceeded. "
                "Delete budget.lock via Dashboard or `unlock` to resume.",
                duty,
            )
        if duty == "strategic" and os.path.exists(config.SOFT_LOCK_FILE):
            console.print("[yellow]⚠ SOFT LOCK (80%): Orchestrator blocked — downgrading to Heavy.[/yellow]")
            return None, "coding"
        return None, duty

    def _try_local(self, prompt: str, task_summary: str) -> tuple | None:
        """
        Attempt local (Ollama) execution.
        Returns a complete result tuple on success, None to signal escalation.
        """
        if self._is_local_circuit_open():
            console.print("[yellow]Circuit breaker open — escalating to Cloud Heavy.[/yellow]")
            return None
        try:
            content, tokens = self._run_local_stream(prompt, task_summary)
            log_msg = log_task(self.local_model, "Light", tokens, task_summary, "Success")
            self._record_local_success()
            self._context.append(task_summary)
            _monitor_idle()
            result = {
                "choices":  [{"message": {"content": content}}],
                "model":    self.local_model,
                "tier":     "Light",
                "provider": "Local (Ollama)",
                "tokens":   tokens,
            }
            return result, f"✅ Local ({self.local_model}). {log_msg}"
        except StreamTruncatedError as e:
            console.print(
                f"[yellow]⚠️  Local stream truncated ({e.tokens} tokens received, no [DONE]). "
                f"Escalating to Cloud Heavy.[/yellow]"
            )
            self._record_local_failure()
            return None
        except Exception as e:
            console.print(f"[yellow]⚠️  Local failed: {e}. Escalating to Cloud Heavy.[/yellow]")
            self._record_local_failure()
            return None

    def _build_chain(self, duty: str) -> tuple[list[str], str]:
        """Return (model_chain, tier) for the given duty."""
        if duty == "coding":
            return (
                [
                    "qwen/qwen-2.5-coder-32b-instruct",  # primary  ~$0.07/1M
                    "deepseek/deepseek-chat-v3-0324",     # fallback ~$0.27/1M
                    "anthropic/claude-3.5-sonnet",        # last resort
                ],
                "Heavy",
            )
        return (["anthropic/claude-3.5-sonnet", "openai/gpt-4o"], "Orchestrator")

    def _pre_flight_check(self, tier: str, model_chain: list[str], prompt: str) -> bool:
        """
        Show cost estimate and optionally ask for confirmation.
        Returns True if the user cancelled the call.

        Token estimate accounts for:
          • The prompt itself (chars ÷ 4 ≈ tokens, standard BPE rule-of-thumb)
          • Injected session-context messages that _build_messages() will prepend
          • Estimated output tokens — code generation typically produces tokens
            at ~50% of input volume; using that as a floor avoids the previous
            30–50% undercount that came from counting only prompt words × 1.4
        """
        context_chars     = sum(len(c) for c in self._context[-config.CONTEXT_WINDOW_SIZE:])
        total_input_chars = len(prompt) + context_chars
        est_input_tokens  = max(50, total_input_chars // 4)
        est_output_tokens = max(128, est_input_tokens // 2)   # conservative output floor
        est_tokens        = est_input_tokens + est_output_tokens

        price_per_m = next(
            (v for k, v in config.MODEL_PRICING.items() if k in model_chain[0]),
            config.FALLBACK_COST_PER_1M,
        )
        est_cost = (est_tokens / 1_000_000) * price_per_m

        if tier == "Orchestrator":
            from rich.prompt import Confirm
            console.print(
                f"[dim]💰 Est. ~{est_tokens:,} tokens → "
                f"{model_chain[0].split('/')[-1]} → ~${est_cost:.4f}[/dim]"
            )
            if est_cost > config.PRE_FLIGHT_USD_THRESHOLD:
                return not Confirm.ask("Proceed with Orchestrator call?", default=True)
        elif tier == "Heavy" and est_cost > config.PRE_FLIGHT_USD_THRESHOLD:
            console.print(f"[dim]💰 Est. ~{est_tokens:,} tokens → ~${est_cost:.4f}[/dim]")
        return False

    def _check_cache(
        self, prompt: str, model: str, tier: str, task_summary: str
    ) -> tuple | None:
        """Return a cached result tuple or None on a cache miss."""
        cached = response_cache.get(prompt, model, tier)
        if not cached:
            return None
        console.print(f"[dim]⚡ Cache hit ({tier}) — skipping API call.[/dim]")
        provider = infer_provider(cached["model"])
        log_msg  = log_task(
            model=cached["model"], tier=tier, tokens=cached["tokens"],
            task=task_summary, status="Cache",
        )
        _monitor_idle()
        return (
            {
                "choices":  [{"message": {"content": cached["content"]}}],
                "model":    cached["model"],
                "tier":     tier,
                "provider": provider,
                "tokens":   cached["tokens"],
            },
            f"⚡ Cache hit — {cached['model']}. {log_msg}",
        )

    def _execute_cloud(
        self, prompt: str, model_chain: list[str], tier: str, task_summary: str
    ) -> tuple:
        """Stream a cloud request, log it, cache it, and return a result tuple."""
        try:
            messages                   = self._build_messages(prompt)
            content, used_model, tokens = self._run_cloud_stream(
                messages, model_chain, tier, task_summary
            )

            if "claude" in used_model.lower() and tier == "Heavy":
                console.print(
                    f"[bold yellow]⚠️  COST ESCALATION: fell to {used_model}. "
                    f"Tokens: {tokens}.[/bold yellow]"
                )

            response_cache.put(prompt, used_model, tier, content, tokens)
            self._context.append(task_summary)

            provider = infer_provider(used_model)
            log_msg  = log_task(
                model=used_model, tier=tier, tokens=tokens,
                task=task_summary, status="Success",
            )
            _monitor_idle()
            return (
                {
                    "choices":  [{"message": {"content": content}}],
                    "model":    used_model,
                    "tier":     tier,
                    "provider": provider,
                    "tokens":   tokens,
                },
                f"✅ {used_model}. {log_msg}",
            )

        except StreamTruncatedError as e:
            # Cloud gave us partial content — surface it with a warning rather
            # than discarding the entire (potentially expensive) API call.
            console.print(
                f"[bold yellow]⚠️  Cloud stream truncated — partial response returned "
                f"({e.tokens} tokens, {len(e.partial_content):,} chars).[/bold yellow]"
            )
            # Best-effort: use the primary model name since headers may be gone
            partial_model = model_chain[0]
            response_cache.put(prompt, partial_model, tier, e.partial_content, e.tokens)
            self._context.append(task_summary)
            log_task(model=partial_model, tier=tier, tokens=e.tokens,
                     task=task_summary, status="Partial")
            _monitor_idle()
            return (
                {
                    "choices":  [{"message": {"content": e.partial_content}}],
                    "model":    partial_model,
                    "tier":     tier,
                    "provider": infer_provider(partial_model),
                    "tokens":   e.tokens,
                },
                f"⚠️ Partial response — stream truncated after {e.tokens} tokens.",
            )
        except KeyboardInterrupt:
            _monitor_idle()
            raise
        except requests.exceptions.RequestException as e:
            log_task(model="unknown", tier=tier, tokens=0, task=task_summary, status="Fallback/Failed")
            return None, f"CRITICAL FAILURE: All models failed. Error: {str(e)}"
        except Exception as e:
            log_task(model="unknown", tier=tier, tokens=0, task=task_summary, status="Fallback/Failed")
            return None, f"Unexpected error: {str(e)}"

    # ── Public entry point ─────────────────────────────────────────────────────

    def execute_task(self, prompt: str) -> tuple:
        """
        Route → budget gate → local → cloud chain → cache → stream.
        Returns (result_dict | None, status_str).
        """
        task_summary = (prompt[:60] + "...") if len(prompt) > 60 else prompt

        # 1. Route
        duty, target, reason = self.router.route_request(prompt)
        console.print(Rule(f"[bold]🔀 {duty.upper()} → {target}[/bold]", style="dim"))
        console.print(f"[dim]{reason}[/dim]\n")

        # 2. Budget gate (may downgrade duty from strategic → coding)
        block, duty = self._budget_gate(duty)
        if block:
            return None, block

        # 3. Local path
        if duty == "local":
            local_result = self._try_local(prompt, task_summary)
            if local_result is not None:
                return local_result
            duty = "coding"   # escalate to cloud

        # 4. Build model chain and tier label
        model_chain, tier = self._build_chain(duty)

        # 5. Pre-flight cost estimate (may ask for confirmation)
        if self._pre_flight_check(tier, model_chain, prompt):
            return None, "Cancelled by user."

        # 6. Cache probe
        cached = self._check_cache(prompt, model_chain[0], tier, task_summary)
        if cached:
            return cached

        # 7. Cloud execution
        return self._execute_cloud(prompt, model_chain, tier, task_summary)


# ── Standalone smoke-test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if config.OPENROUTER_API_KEY:
        orch = Orchestrator()
        _, status = orch.execute_task("Format the docstrings in utils.py")
        print(status)
    else:
        console.print("[red]Please configure OPENROUTER_API_KEY in .env before running.[/red]")
