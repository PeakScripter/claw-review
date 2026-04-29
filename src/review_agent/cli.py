"""CLI entry point for the review agent.

Usage:
    # Interactive REPL:
    review

    # Single reviewer headless:
    review --task diff:main..HEAD --format markdown

    # Parallel coordinator:
    review --task diff:main..HEAD --reviewers security,correctness,tests

    # All reviewers, SARIF output:
    review --task diff --reviewers all --format sarif

    # NDJSON event stream:
    review --task files:src/foo.py --print
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from review_agent.coordinator import ParallelCoordinator, build_default_registry
from review_agent.engine import ReviewEngine
from review_agent.findings.format import format_github, format_json, format_markdown, format_sarif
from review_agent.llm.groq_client import GroqClient, config_from_env
from review_agent.skills.loader import load_reviewer
from review_agent.types import (
    AssistantTextEvent,
    DiffTask,
    ErrorEvent,
    FilesTask,
    FinalEvent,
    FindingEvent,
    PRTask,
    RepoTask,
    ReviewTask,
    ToolResultEvent,
    ToolUseEvent,
)

app = typer.Typer(
    add_completion=False,
    help="Read-only AI code review agent.",
    no_args_is_help=True,
)
console = Console(stderr=True)

ALL_REVIEWERS = ["correctness", "security", "performance", "style", "tests", "architecture"]


@app.command()
def main(
    task: str | None = typer.Option(
        None,
        "--task",
        help="Task spec: `diff[:base..head]`, `files:p1,p2,...`, `pr:owner/repo#N`, or `repo`. "
             "Omit to start the interactive REPL.",
    ),
    cwd: Path = typer.Option(
        Path.cwd(),
        "--cwd",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Working directory for the review.",
    ),
    output_format: str = typer.Option(
        "markdown",
        "--format",
        help="Output format: markdown, json, or events (NDJSON).",
    ),
    reviewer: str = typer.Option(
        "correctness",
        "--reviewer",
        help="Single reviewer to use (ignored if --reviewers is set).",
    ),
    reviewers: str | None = typer.Option(
        None,
        "--reviewers",
        help=(
            "Comma-separated list of reviewers to run in parallel. "
            "Use `all` for every reviewer. "
            "E.g. --reviewers security,correctness,tests"
        ),
    ),
    model: str = typer.Option(
        "openai/gpt-oss-120b",
        "--model",
        help="Groq model id for the coordinator / single reviewer.",
    ),
    sub_model: str | None = typer.Option(
        None,
        "--sub-model",
        help="Groq model id for sub-reviewers (defaults to llama-3.3-70b-versatile).",
    ),
    print_events: bool = typer.Option(
        False,
        "--print",
        help="Stream events as NDJSON to stdout. Forces --format events.",
    ),
    max_iterations: int = typer.Option(12, "--max-iterations", min=1, max=64),
    min_confidence: float = typer.Option(
        0.5,
        "--min-confidence",
        min=0.0,
        max=1.0,
        help="Drop findings below this confidence score before reporting (0.0–1.0).",
    ),
) -> None:
    """Run a code review. Use --reviewers for parallel multi-specialist mode. Omit --task to start the interactive REPL."""
    # No task → launch REPL.
    if task is None:
        try:
            groq_config = config_from_env(model=model)
        except Exception as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from e
        reviewer_list = (
            ALL_REVIEWERS if reviewers and reviewers.strip().lower() == "all"
            else [r.strip() for r in reviewers.split(",") if r.strip()]
            if reviewers
            else ["correctness"]
        )
        from review_agent.repl import ReviewREPL
        repl = ReviewREPL(
            groq=GroqClient(groq_config),
            cwd=cwd,
            default_reviewers=reviewer_list,
            sub_model=sub_model or "llama-3.3-70b-versatile",
        )
        repl.run()
        return

    parsed_task = _parse_task(task, cwd)
    payload = _materialize_payload(parsed_task, cwd)
    if payload is None:
        console.print("[red]Could not materialize the review payload.[/red]")
        raise typer.Exit(code=2)

    try:
        groq_config = config_from_env(model=model)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    registry = build_default_registry()
    groq = GroqClient(groq_config)

    # Determine reviewer list.
    reviewer_list: list[str] | None = None
    if reviewers:
        if reviewers.strip().lower() == "all":
            reviewer_list = ALL_REVIEWERS
        else:
            reviewer_list = [r.strip() for r in reviewers.split(",") if r.strip()]

    final = asyncio.run(
        _run_coordinator(
            groq=groq,
            registry=registry,
            cwd=cwd,
            task=parsed_task,
            payload=payload,
            reviewer_list=reviewer_list,
            single_reviewer=reviewer,
            sub_model=sub_model or "llama-3.3-70b-versatile",
            print_events=print_events,
            max_iterations=max_iterations,
            min_confidence=min_confidence,
        )
        if reviewer_list
        else _run_single(
            groq=groq,
            registry=registry,
            cwd=cwd,
            task=parsed_task,
            payload=payload,
            reviewer_name=reviewer,
            print_events=print_events,
            max_iterations=max_iterations,
            min_confidence=min_confidence,
        )
    )

    if print_events or output_format == "events":
        return

    findings = final.findings if final else []
    if output_format == "markdown":
        sys.stdout.write(format_markdown(findings))
    elif output_format == "json":
        sys.stdout.write(format_json(findings) + "\n")
    elif output_format == "sarif":
        sys.stdout.write(format_sarif(findings) + "\n")
    elif output_format == "github":
        sys.stdout.write(format_github(findings) + "\n")
    else:
        console.print(f"[red]Unknown format: {output_format!r}[/red]")
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# Internal runners
# ---------------------------------------------------------------------------


async def _run_single(
    *,
    groq: GroqClient,
    registry,
    cwd: Path,
    task: ReviewTask,
    payload: str,
    reviewer_name: str,
    print_events: bool,
    max_iterations: int,
    min_confidence: float = 0.5,
) -> FinalEvent | None:
    try:
        manifest = load_reviewer(reviewer_name)
    except FileNotFoundError:
        console.print(f"[red]Reviewer not found: {reviewer_name!r}[/red]")
        raise typer.Exit(code=2)

    engine = ReviewEngine(
        groq=groq,
        registry=registry,
        cwd=cwd,
        reviewer_name=manifest.name,
        reviewer_instructions=manifest.instructions,
        max_iterations=max_iterations,
    )
    return await _drain(engine.review(task, payload, min_confidence=min_confidence), print_events)


async def _run_coordinator(
    *,
    groq: GroqClient,
    registry,
    cwd: Path,
    task: ReviewTask,
    payload: str,
    reviewer_list: list[str],
    single_reviewer: str,
    sub_model: str,
    print_events: bool,
    max_iterations: int,
    min_confidence: float = 0.5,
) -> FinalEvent | None:
    console.print(
        f"[bold]Running {len(reviewer_list)} reviewer(s) in parallel:[/bold] "
        + ", ".join(reviewer_list)
    )
    coord = ParallelCoordinator(
        groq=groq,
        registry=registry,
        cwd=cwd,
        reviewer_names=reviewer_list,
        sub_reviewer_model=sub_model,
        max_iterations=max_iterations,
    )
    return await _drain(coord.review(task, payload, min_confidence=min_confidence), print_events)


async def _drain(stream, print_events: bool) -> FinalEvent | None:
    final: FinalEvent | None = None
    async for event in stream:
        if print_events:
            sys.stdout.write(event.model_dump_json() + "\n")
            sys.stdout.flush()
        else:
            _render_event(event)
        if isinstance(event, FinalEvent):
            final = event
    return final


def _render_event(event) -> None:
    """Lightweight live progress on stderr so stdout stays clean for the report."""
    t = event.type
    if t == "tool_use":
        console.print(f"[dim]  → {event.tool}({_brief_args(event.input)})[/dim]")
    elif t == "tool_result":
        marker = "[green]✓[/green]" if event.ok else "[red]✗[/red]"
        console.print(f"    {marker} {event.tool}")
    elif t == "finding":
        f = event.finding
        sev_color = {"critical": "red", "high": "bright_red", "medium": "yellow"}.get(
            f.severity, "white"
        )
        console.print(
            f"  [{sev_color}]●[/{sev_color}] [{f.severity}] "
            f"{f.file}:{f.line} — {f.title}"
        )
    elif t == "assistant_text":
        if event.text.strip():
            console.print(f"[dim]{event.text.strip()}[/dim]")
    elif t == "error":
        console.print(f"[red]  error: {event.message}[/red]")


def _brief_args(args: dict) -> str:
    if not args:
        return ""
    pairs = []
    for k, v in list(args.items())[:3]:
        sv = json.dumps(v) if not isinstance(v, str) else v
        if len(sv) > 40:
            sv = sv[:40] + "…"
        pairs.append(f"{k}={sv}")
    return ", ".join(pairs)


# ---------------------------------------------------------------------------
# Task parsing and payload materialisation
# ---------------------------------------------------------------------------


def _parse_task(spec: str, cwd: Path) -> ReviewTask:
    if spec == "repo":
        return RepoTask(cwd=str(cwd))
    if spec == "diff" or spec.startswith("diff:"):
        rest = spec[5:] if spec.startswith("diff:") else "main..HEAD"
        if ".." not in rest:
            raise typer.BadParameter("diff task needs `base..head`, e.g. diff:main..HEAD")
        base, head = rest.split("..", 1)
        return DiffTask(base=base, head=head, cwd=str(cwd))
    if spec.startswith("files:"):
        paths = [p.strip() for p in spec[len("files:"):].split(",") if p.strip()]
        if not paths:
            raise typer.BadParameter("files task needs at least one path")
        return FilesTask(paths=paths, cwd=str(cwd))
    if spec.startswith("pr:"):
        rest = spec[len("pr:"):]
        if "#" not in rest:
            raise typer.BadParameter("pr task format is `pr:owner/repo#N`")
        repo, num = rest.rsplit("#", 1)
        return PRTask(repo=repo, number=int(num))
    raise typer.BadParameter(f"unrecognized task spec: {spec!r}")


def _materialize_payload(task: ReviewTask, cwd: Path) -> str | None:
    if isinstance(task, DiffTask):
        try:
            out = subprocess.run(
                ["git", "-C", str(cwd), "--no-pager", "diff", "--no-color",
                 f"{task.base}..{task.head}"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]git diff failed: {e.stderr.strip()}[/red]")
            return None
        diff = out.stdout
        if not diff.strip():
            console.print(
                f"[yellow]No diff between {task.base} and {task.head}.[/yellow]"
            )
            return "(empty diff — nothing to review)"
        return f"### Diff `{task.base}..{task.head}`\n\n```diff\n{diff}\n```"
    if isinstance(task, FilesTask):
        return (
            "Files to review:\n"
            + "\n".join(f"- `{p}`" for p in task.paths)
            + "\n\nUse `read_file` to fetch their contents."
        )
    if isinstance(task, RepoTask):
        return "Whole-repo review. Use `glob` to enumerate files and `read_file` to inspect."
    if isinstance(task, PRTask):
        return f"(PR fetch not implemented in Phase 1; would fetch {task.repo}#{task.number})"
    return None


if __name__ == "__main__":
    app()
