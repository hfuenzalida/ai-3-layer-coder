import json
import os
import py_compile
import re
import subprocess
import tempfile
from datetime import datetime
import config
from orchestrator import Orchestrator
from session_stats import calculate_cost
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.prompt import Confirm
from rich import box

console = Console()


# ── Standalone utilities (importable by codex.py) ─────────────────────────────

def save_file(path: str, content: str) -> tuple[bool, str]:
    """
    Write content to path, enforcing the OUTPUT_DIR whitelist.
    Returns (success, message).
    """
    if not path:
        return True, "No path given — nothing to save."

    allowed_root = os.path.realpath(config.OUTPUT_DIR)
    resolved     = os.path.realpath(os.path.abspath(path))
    if not (resolved == allowed_root or resolved.startswith(allowed_root + os.sep)):
        return False, (
            f"⛔ Blocked: '{path}' resolves outside OUTPUT_DIR.\n"
            f"   Allowed root: {allowed_root}\n"
            f"   Set OUTPUT_DIR in .env to change it."
        )

    try:
        cleaned = _strip_markdown_fence(content)
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(cleaned)
        return True, f"💾 Saved {len(cleaned.encode('utf-8')):,} bytes → {resolved}"
    except Exception as e:
        return False, f"⚠ Failed to save: {e}"


def validate_python(content: str) -> str | None:
    """
    Compile-check Python source. Returns an error string on failure, None if clean.
    Strips markdown fences before checking.

    Uses TemporaryDirectory so both the .py source file and the __pycache__/*.pyc
    bytecode file written by py_compile are cleaned up in all exit paths — including
    a crash inside py_compile that would otherwise leave orphaned files on Windows.
    """
    src = _strip_markdown_fence(content)
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = os.path.join(tmp_dir, "_codex_check.py")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(src)
            try:
                py_compile.compile(tmp_path, doraise=True)
                return None
            except py_compile.PyCompileError as e:
                return str(e)
    except Exception:
        return None


def _strip_markdown_fence(content: str) -> str:
    """
    Extract the first fenced code block from content, regardless of where it appears.

    Handles the common model behaviour of prefixing code with a short explanation:
        "Here is the implementation:\n```python\n...\n```"

    The optional language tag after the opening fence (e.g. ```python) is consumed
    but not returned.  Falls back to returning the original string unchanged when no
    fence pair is found so callers always get something usable.
    """
    match = re.search(r"```(?:\w+)?\n(.*?)```", content, re.DOTALL)
    return match.group(1) if match else content


# ── WorkflowEngine ─────────────────────────────────────────────────────────────

class WorkflowEngine:
    def __init__(self, orchestrator: "Orchestrator | None" = None):
        """
        Accept an existing Orchestrator so the workflow shares the session's
        context memory and connection pool. Falls back to a fresh instance
        only when called standalone (e.g. python task_executor.py).
        """
        self.orchestrator = orchestrator if orchestrator is not None else Orchestrator()

    def generate_plan(self, main_objective: str) -> list[dict]:
        """
        Decompose an objective into a structured list of sub-tasks by delegating
        to the Orchestrator.  This respects the routing system (budget locks,
        circuit breakers, caching) instead of bypassing it with a hardcoded model.

        The prompt asks for strict JSON so we can parse it reliably; the regex
        fallback handles any model that wraps the JSON in a markdown fence.
        """
        console.print("\n[bold cyan]🧠 Architecting plan...[/bold cyan]")

        prompt = (
            "You are an elite Software Architecture Agent.\n"
            "Break down the objective below into actionable sub-tasks.\n"
            "DO NOT WRITE FULL CODE.\n"
            "Output ONLY a raw JSON object — no markdown, no explanation — matching this schema:\n\n"
            '{"tasks": [{"id": "1", "action": "...", "file": "path/to/file.py",'
            ' "type": "logic|boilerplate|refactor|html|css"}]}\n\n'
            f"Objective: {main_objective}"
        )

        result, status = self.orchestrator.execute_task(prompt)
        if result is None:
            console.print(f"[bold red]🚨 Failed to generate plan: {status}[/bold red]")
            return []

        raw = result["choices"][0]["message"]["content"]

        # Try direct parse first; fall back to extracting JSON from a fenced block
        for candidate in (raw, _strip_markdown_fence(raw)):
            try:
                return json.loads(candidate).get("tasks", [])
            except (json.JSONDecodeError, AttributeError):
                pass

        # Last resort: find the first {...} object anywhere in the response
        obj_match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group()).get("tasks", [])
            except (json.JSONDecodeError, AttributeError):
                pass

        console.print("[bold red]🚨 Could not parse plan JSON from response.[/bold red]")
        return []

    def _preview_plan(self, tasks: list[dict]) -> bool:
        table = Table(
            box=box.ROUNDED, show_header=True, header_style="bold dim",
            title=f"[bold]Plan — {len(tasks)} sub-tasks[/bold]",
        )
        table.add_column("#",      style="dim",    width=4)
        table.add_column("Action", style="cyan")
        table.add_column("File",   style="dim")
        table.add_column("Type",   style="yellow", width=12)
        for t in tasks:
            table.add_row(str(t.get("id","?")), t.get("action",""),
                          t.get("file","—"), t.get("type","—"))
        console.print()
        console.print(table)
        console.print()
        return Confirm.ask("[bold]Execute this plan?[/bold]", default=False)

    def execute_workflow(
        self,
        main_objective: str,
        max_cost_usd: float = None,
    ) -> list[dict]:
        """
        Plans, validates, and executes a multi-step workflow.
        Returns task result dicts for session tracking.

        max_cost_usd: stop mid-workflow if cumulative task spend exceeds this
        value.  Defaults to config.WORKFLOW_MAX_COST_USD ($5.00 unless
        overridden via env var).  Pass float('inf') to disable the cap.
        """
        if max_cost_usd is None:
            max_cost_usd = config.WORKFLOW_MAX_COST_USD

        tasks = self.generate_plan(main_objective)
        if not tasks:
            console.print("[yellow]No actionable plan received.[/yellow]")
            return []

        if not self._preview_plan(tasks):
            console.print("[dim]Plan cancelled.[/dim]\n")
            return []

        console.print(
            f"[bold]Delegating {len(tasks)} sub-tasks...[/bold]  "
            f"[dim](budget cap: ${max_cost_usd:.2f})[/dim]\n"
        )

        task_results  = []
        failed_tasks  = []
        workflow_cost = 0.0

        for i, task in enumerate(tasks, 1):
            task_id   = task.get("id", str(i))
            action    = task.get("action", "")
            file_path = task.get("file", "")

            console.print(Rule(f"[bold]Task {task_id}/{len(tasks)}: {action[:70]}[/bold]", style="dim"))

            prompt = (
                f"Implement the following. Output ONLY the raw code for the file, nothing else.\n"
                f"Task: {action}\n"
                f"Target File: {file_path}"
            )

            result, status = self.orchestrator.execute_task(prompt)
            timestamp = datetime.now().isoformat()

            # ── Per-workflow budget guard ──────────────────────────────────
            if workflow_cost >= max_cost_usd:
                remaining = len(tasks) - i + 1
                console.print(
                    f"[bold yellow]⚠ Workflow budget cap ${max_cost_usd:.2f} reached after "
                    f"${workflow_cost:.4f} — stopping. "
                    f"{remaining} task(s) skipped.[/bold yellow]"
                )
                for skipped in tasks[i - 1:]:
                    failed_tasks.append({
                        "id":     skipped.get("id", "?"),
                        "action": skipped.get("action", ""),
                        "reason": f"Budget cap ${max_cost_usd:.2f} reached",
                    })
                break

            if result is None:
                console.print(f"[bold red]✗ Task {task_id} failed: {status}[/bold red]")
                failed_tasks.append({"id": task_id, "action": action, "reason": status})
                task_results.append({
                    "summary": action[:60], "model": "unknown", "tier": "Unknown",
                    "provider": "", "tokens": 0, "timestamp": timestamp, "status": "Failed",
                })
                continue

            code_content = result["choices"][0]["message"]["content"]

            # ── Syntax validation + one auto-retry for Python files ────────
            if file_path.endswith(".py"):
                err = validate_python(code_content)
                if err:
                    console.print(f"[yellow]⚠ Syntax error in Task {task_id} — retrying...[/yellow]")
                    console.print(f"[dim]  {err}[/dim]")
                    retry_prompt = (
                        f"{prompt}\n\n"
                        f"The previous attempt had a syntax error:\n{err}\n"
                        f"Fix it and output ONLY the corrected code."
                    )
                    retry_result, _ = self.orchestrator.execute_task(retry_prompt)
                    if retry_result:
                        retry_content = retry_result["choices"][0]["message"]["content"]
                        err2 = validate_python(retry_content)
                        if err2:
                            # Retry also broken — block the save entirely
                            console.print(
                                f"[bold red]✗ Task {task_id}: retry still has a syntax error "
                                f"— file NOT saved.[/bold red]"
                            )
                            console.print(f"[dim]  {err2}[/dim]")
                            reason = f"Syntax error persisted after retry: {err2[:80]}"
                            failed_tasks.append({"id": task_id, "action": action, "reason": reason})
                            task_results.append({
                                "summary": action[:60], "model": retry_result.get("model", "unknown"),
                                "tier": retry_result.get("tier", "Unknown"),
                                "provider": retry_result.get("provider", ""),
                                "tokens": retry_result.get("tokens", 0),
                                "timestamp": timestamp, "status": "Failed",
                            })
                            continue
                        # Retry clean — use the fixed code and its metadata
                        code_content = retry_content
                        result = retry_result

            saved, save_msg = save_file(file_path, code_content)
            console.print(f"[dim]{save_msg}[/dim]")

            if not saved:
                failed_tasks.append({"id": task_id, "action": action, "reason": save_msg})

            task_tokens = result.get("tokens", 0)
            task_cost   = calculate_cost(result.get("model", "unknown"), task_tokens)
            workflow_cost += task_cost
            task_results.append({
                "summary":  action[:60],
                "model":    result.get("model", "unknown"),
                "tier":     result.get("tier", "Unknown"),
                "provider": result.get("provider", ""),
                "tokens":   task_tokens,
                "timestamp": timestamp,
                "status":   "Success" if saved else "Partial",
            })
            console.print(
                f"[dim]  Workflow spend so far: ${workflow_cost:.4f} / ${max_cost_usd:.2f}[/dim]"
            )

        succeeded = len(tasks) - len(failed_tasks)
        failure_lines = "\n".join(
            f"  • Task {ft['id']} — {ft['action'][:50]}: {ft['reason'][:60]}"
            for ft in failed_tasks
        )
        summary_body = f"[bold green]✓ {succeeded}/{len(tasks)} tasks succeeded[/bold green]"
        if failed_tasks:
            summary_body += f"\n[yellow]⚠ {len(failed_tasks)} failed:[/yellow]\n{failure_lines}"

        console.print()
        console.print(Panel(
            summary_body,
            title="[bold]Workflow Complete[/bold]",
            border_style="green" if not failed_tasks else "yellow",
        ))
        console.print()

        # ── Git commit offer ───────────────────────────────────────────────
        if succeeded > 0 and Confirm.ask("Commit changes with git?", default=False):
            self._git_commit(main_objective[:72])

        return task_results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save_to_disk(self, path: str, content: str) -> bool:
        """Thin wrapper around the module-level save_file()."""
        ok, msg = save_file(path, content)
        console.print(f"[dim]{msg}[/dim]")
        return ok

    def _git_commit(self, message: str) -> None:
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("[dim]Not a git repository or git not found — skipping commit.[/dim]")
            return

        try:
            # Stage only the files inside OUTPUT_DIR — never blindly stage the
            # whole working tree, which could include .env or other sensitive files.
            subprocess.run(
                ["git", "add", "--", config.OUTPUT_DIR],
                check=True, capture_output=True,
            )
            r = subprocess.run(
                ["git", "commit", "-m", f"codex: {message}"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                sha = r.stdout.strip().split("\n")[0]
                console.print(f"[bold green]✓ Git commit:[/bold green] [dim]{sha}[/dim]")
            else:
                console.print(f"[yellow]⚠ Git commit failed: {r.stderr.strip()}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]⚠ Git error: {e}[/yellow]")


if __name__ == "__main__":
    if config.OPENROUTER_API_KEY:
        engine = WorkflowEngine()
        engine.execute_workflow("Create a python script that prints hello world to hello.py")
    else:
        console.print("[red]Please configure OPENROUTER_API_KEY in .env before running.[/red]")
