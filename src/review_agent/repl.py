"""Rich-based interactive REPL."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Fall back to plain ASCII when the terminal encoding can't handle Unicode symbols.
def _unicode_ok() -> bool:
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "◆❯✓✗⠿━░▓█↑↓●→".encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False

_UNI    = _unicode_ok()
_BULLET = "◆" if _UNI else "*"
_PROMPT = "❯" if _UNI else ">"
_TICK   = "✓" if _UNI else "+"
_CROSS  = "✗" if _UNI else "x"
_SPIN   = "⠿" if _UNI else "~"
_DOT    = "●" if _UNI else "o"
_ARROW  = "→" if _UNI else "->"
_DASH   = "━" if _UNI else "-"

from review_agent import __version__
from review_agent.commands import CommandRegistry, register_builtin_commands
from review_agent.coordinator import ParallelCoordinator, build_default_registry
from review_agent.findings.model import Severity
from review_agent.findings.store import FindingStore
from review_agent.hooks.runner import HookEvent, load_hooks
from review_agent.llm.groq_client import GroqClient
from review_agent.types import FinalEvent, FindingEvent

_SEVERITY_COLOR: dict[Severity, str] = {
    "critical": "bold red",
    "high":     "red",
    "medium":   "yellow",
    "low":      "blue",
    "info":     "dim",
}

_SEVERITY_LABEL: dict[str, str] = {
    "critical": "CRIT",
    "high":     "HIGH",
    "medium":   "MED ",
    "low":      "LOW ",
    "info":     "INFO",
}

_COMMANDS_HELP = [
    ("/review-diff [base..head]", "Review the current branch diff"),
    ("/review-pr <num>",          "Review a GitHub PR  (needs GITHUB_TOKEN)"),
    ("/review-files f1,f2",       "Review specific files"),
    ("/findings",                 "List findings from the last review"),
    ("/export <format>",          "Export: markdown · json · sarif · github"),
    ("/help",                     "All commands"),
    ("exit  ·  Ctrl+C",           "Quit"),
]

_SLASH_WORDS = [
    "/review-diff", "/review-pr", "/review-files", "/security-scan",
    "/findings", "/export", "/help", "/cost", "/clear", "/ignore", "/explain",
    "exit",
]


# ---------------------------------------------------------------------------
# Git helpers for welcome screen
# ---------------------------------------------------------------------------

def _git_branch(cwd: Path) -> str | None:
    """Return the current Git branch name, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _git_repo_name(cwd: Path) -> str | None:
    """Return the repo name from the remote URL, or the folder name."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            url = out.stdout.strip()
            # Handle both HTTPS and SSH URLs
            name = url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name
    except Exception:
        pass
    return cwd.name


def _git_dirty_count(cwd: Path) -> int:
    """Return number of uncommitted changed files."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return len([l for l in out.stdout.strip().splitlines() if l.strip()])
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Welcome screen
# ---------------------------------------------------------------------------

_LOGO_LINES = [
    r"     ___  ____ _   _ _ ____ _ _ _",
    r"     |__] |___ \  / | |___ | | |",
    r"     |  \ |___  \/  | |___ |_|_|",
]

# Cute little bot — displayed to the right of the logo.
_BOT_ART = [
    "   .--.",
    "  |o_o |",
    "  |:_/ |",
    " //   \\ \\",
    "(|     | )",
    "/'\\_   _/`\\",
    "\\___)=(___/",
]

_BOT_ART_MINI = [
    " [o_o]",
    " /|  |\\",
    "  d  b",
]


def _print_welcome(console: Console, *, cwd: Path | None = None, model: str = "") -> None:
    """Print a Claude-Code-style welcome banner with context info."""
    width = min(console.width, 80)

    # ── Blank line for breathing room ──
    console.print()

    # ── Logo + Bot side-by-side ──
    logo = Text()
    for line in _LOGO_LINES:
        logo.append(line + "\n", style="bold cyan")
    logo.rstrip()

    bot_lines = _BOT_ART if width >= 65 else _BOT_ART_MINI
    bot = Text()
    for line in bot_lines:
        bot.append(line + "\n", style="bold yellow")
    bot.rstrip()

    # Pad the shorter block so they vertically align at the bottom.
    logo_h = len(_LOGO_LINES)
    bot_h = len(bot_lines)
    if bot_h > logo_h:
        pad_lines = "\n" * (bot_h - logo_h)
        logo = Text(pad_lines) + logo
    elif logo_h > bot_h:
        pad_lines = "\n" * (logo_h - bot_h)
        bot = Text(pad_lines) + bot

    if width >= 55:
        console.print(Padding(Columns([logo, Text("   "), bot], padding=(0, 0)), (0, 3)))
    else:
        console.print(Padding(logo, (0, 3)))

    # ── Tagline ──
    tagline = Text()
    tagline.append(f"  {_BULLET} ", style="cyan")
    tagline.append("review-agent", style="bold white")
    tagline.append(f"  v{__version__}", style="dim")
    tagline.append("  —  ", style="dim")
    tagline.append("AI-powered code review", style="dim italic")
    console.print(tagline)
    console.print()

    # ── Context info (model, branch, cwd) ──
    info_lines: list[Text] = []

    if model:
        t = Text()
        t.append(f"  {_DOT} ", style="magenta")
        t.append("model  ", style="dim")
        t.append(model, style="bold magenta")
        info_lines.append(t)

    if cwd:
        repo_name = _git_repo_name(cwd) or cwd.name
        branch = _git_branch(cwd)
        dirty = _git_dirty_count(cwd)

        t = Text()
        t.append(f"  {_DOT} ", style="green")
        t.append("repo   ", style="dim")
        t.append(repo_name, style="bold green")
        if branch:
            t.append(f"  ({branch})", style="yellow")
        if dirty:
            t.append(f"  [{dirty} changed]", style="dim yellow")
        info_lines.append(t)

        t = Text()
        t.append(f"  {_DOT} ", style="blue")
        t.append("cwd    ", style="dim")
        t.append(str(cwd), style="blue")
        info_lines.append(t)

    for line in info_lines:
        console.print(line)

    if info_lines:
        console.print()

    # ── Separator ──
    sep = Text(_DASH * min(width - 4, 60), style="dim cyan")
    console.print(Padding(sep, (0, 2)))
    console.print()

    # ── Tips / quick start ──
    tips = Text()
    tips.append(f"  {_ARROW} ", style="cyan")
    tips.append("Quick start: ", style="bold white")
    tips.append("type a task spec or use a slash command\n", style="dim")
    tips.append(f"  {_ARROW} ", style="cyan")
    tips.append("Try: ", style="bold white")
    tips.append("/review-diff", style="cyan")
    tips.append("  to review your current branch against main\n", style="dim")
    tips.append(f"  {_ARROW} ", style="cyan")
    tips.append("Or:  ", style="bold white")
    tips.append("diff:main..HEAD", style="cyan")
    tips.append("  as a raw task spec", style="dim")
    console.print(tips)
    console.print()

    # ── Commands table ──
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 3, 0, 0),
        expand=False,
        title="[dim bold]Commands[/dim bold]",
        title_style="dim",
        title_justify="left",
    )
    table.add_column("cmd", style="cyan", no_wrap=True, min_width=28)
    table.add_column("desc", style="dim")
    for cmd, desc in _COMMANDS_HELP:
        table.add_row(cmd, desc)

    console.print(Padding(table, (0, 2)))
    console.print()

    # ── Footer hint ──
    _UP_DN = "↑/↓" if _UNI else "Up/Dn"
    footer = Text()
    footer.append("  [", style="dim")
    footer.append("Tab", style="bold dim cyan")
    footer.append("] autocomplete  ", style="dim")
    footer.append("[", style="dim")
    footer.append(_UP_DN, style="bold dim cyan")
    footer.append("] history  ", style="dim")
    footer.append("[", style="dim")
    footer.append("Ctrl+C", style="bold dim cyan")
    footer.append("] quit", style="dim")
    console.print(footer)
    console.print()


# ---------------------------------------------------------------------------
# prompt_toolkit session (optional — graceful fallback if not installed)
# ---------------------------------------------------------------------------

def _make_pt_session():
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.styles import Style

        style = Style.from_dict({"prompt": "ansicyan bold", "": "ansiwhite"})
        completer = WordCompleter(_SLASH_WORDS, sentence=True)
        return PromptSession(
            history=InMemoryHistory(),
            completer=completer,
            style=style,
            complete_while_typing=False,
            mouse_support=False,
        )
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class Session:
    cwd: Path
    findings: FindingStore = field(default_factory=FindingStore)
    token_usage: dict = field(default_factory=dict)
    last_reviewers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

class ReviewREPL:
    def __init__(
        self,
        *,
        groq: GroqClient,
        cwd: Path,
        default_reviewers: list[str],
        sub_model: str = "llama-3.3-70b-versatile",
    ) -> None:
        self.groq = groq
        self.cwd = cwd
        self.default_reviewers = default_reviewers
        self.sub_model = sub_model
        self.console = Console()
        self.session = Session(cwd=cwd)
        self.hooks = load_hooks(cwd)
        self.commands = CommandRegistry()
        register_builtin_commands(self.commands)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        _print_welcome(self.console, cwd=self.cwd, model=self.groq.config.model)
        pt = _make_pt_session()

        while True:
            try:
                if pt is not None:
                    raw = pt.prompt(f"  {_PROMPT} ").strip()
                else:
                    from rich.prompt import Prompt
                    raw = Prompt.ask(f"  [bold cyan]{_PROMPT}[/bold cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print(f"\n  [dim]Bye.[/dim]\n")
                break

            if not raw or raw in ("exit", "quit", "q"):
                self.console.print("  [dim]Bye.[/dim]\n")
                break

            if raw.startswith("/"):
                self._run_slash(raw)
                continue

            self._run_task_from_spec(raw)

    # ------------------------------------------------------------------
    # Slash-command dispatch
    # ------------------------------------------------------------------

    def _run_slash(self, raw: str) -> None:
        """Dispatch a slash command and print the result."""
        parts = raw[1:].split(None, 1)
        name = parts[0].lower()
        arg  = parts[1] if len(parts) > 1 else ""

        if name in ("review-diff", "rd"):
            result = self._review_diff(arg or "main..HEAD")
        elif name in ("review-pr", "rp"):
            result = self._review_pr(arg)
        elif name in ("review-files", "rf"):
            result = self._review_files(arg)
        elif name == "security-scan":
            result = self._security_scan()
        else:
            result = self.commands.dispatch(raw, self.session)

        if result is None:
            return
        # Commands may return a Rich Renderable (Table, Panel…) or a str.
        # Strings that look like Markdown get rendered as such; everything
        # else is printed directly via Rich markup.
        if isinstance(result, str):
            if result.startswith("#") or "**" in result or "```" in result:
                self.console.print(Markdown(result))
            else:
                self.console.print(result)
        else:
            self.console.print(result)

    # ------------------------------------------------------------------
    # Task runners
    # ------------------------------------------------------------------

    def _run_task_from_spec(self, spec: str) -> None:
        from review_agent.types import DiffTask, FilesTask, RepoTask
        try:
            if spec == "repo":
                task = RepoTask(cwd=str(self.cwd))
            elif spec == "diff" or spec.startswith("diff:"):
                rest = spec[5:] if spec.startswith("diff:") else "main..HEAD"
                if ".." not in rest:
                    self.console.print("  [red]diff task needs base..head, e.g. diff:main..HEAD[/red]")
                    return
                base, head = rest.split("..", 1)
                task = DiffTask(base=base, head=head, cwd=str(self.cwd))
            elif spec.startswith("files:"):
                paths = [p.strip() for p in spec[len("files:"):].split(",") if p.strip()]
                if not paths:
                    self.console.print("  [red]files task needs at least one path[/red]")
                    return
                task = FilesTask(paths=paths, cwd=str(self.cwd))
            else:
                self.console.print(
                    f"  [dim]Unknown input.[/dim]  Try [cyan]/review-diff[/cyan] or [cyan]/help[/cyan]."
                )
                return
        except Exception as exc:
            self.console.print(f"  [red]{exc}[/red]")
            return

        result = self._run_review(task, self.default_reviewers)
        if result:
            self.console.print(Markdown(result))

    def _review_diff(self, ref: str) -> str:
        from review_agent.types import DiffTask
        base, head = (ref.split("..", 1) + ["HEAD"])[:2]
        task = DiffTask(base=base, head=head, cwd=str(self.cwd))
        return self._run_review(task, self.default_reviewers)

    def _review_pr(self, arg: str) -> str:
        if not arg.strip():
            return "Usage: `/review-pr <number>` or `/review-pr owner/repo#number`"
        from review_agent.types import PRTask
        if "#" in arg:
            repo, num = arg.rsplit("#", 1)
        else:
            repo, num = "", arg
        task = PRTask(repo=repo.strip(), number=int(num.strip()))
        return self._run_review(task, self.default_reviewers)

    def _review_files(self, arg: str) -> str:
        from review_agent.types import FilesTask
        paths = [p.strip() for p in arg.split(",") if p.strip()]
        if not paths:
            return "Usage: `/review-files file1,file2,...`"
        task = FilesTask(paths=paths, cwd=str(self.cwd))
        return self._run_review(task, self.default_reviewers)

    def _security_scan(self) -> str:
        from review_agent.types import RepoTask
        task = RepoTask(cwd=str(self.cwd))
        return self._run_review(task, ["security"])

    def _run_review(self, task, reviewer_names: list[str]) -> str:
        hook_result = self.hooks.fire(HookEvent.PRE_REVIEW, {"task": str(task)})
        if hook_result.blocked:
            return f"[PreReview hook blocked the review]\n{hook_result.reason}"

        self.session.findings = FindingStore()
        payload = self._materialize(task)
        if payload is None:
            return "  [red]Could not build the review payload.[/red]"

        registry = build_default_registry()
        coord = ParallelCoordinator(
            groq=self.groq,
            registry=registry,
            cwd=self.cwd,
            reviewer_names=reviewer_names,
            sub_reviewer_model=self.sub_model,
        )
        coord.shared_findings = self.session.findings

        self.console.print()
        final = asyncio.run(self._stream_review(coord, task, payload))
        self.console.print()

        findings = final.findings if final else []
        summary = f"\n**Review complete** — {len(findings)} finding(s).\n"

        self.hooks.fire(HookEvent.POST_REVIEW, {
            "findings": [f.model_dump() for f in findings]
        })

        if findings:
            from review_agent.findings.format.markdown import format_markdown
            return summary + "\n" + format_markdown(findings)
        return summary + "\nNo findings."

    async def _stream_review(self, coord: ParallelCoordinator, task, payload: str) -> FinalEvent | None:
        final = None
        async for event in coord.review(task, payload):
            self._render_event(event)
            if isinstance(event, FinalEvent):
                final = event
            elif isinstance(event, FindingEvent):
                self.hooks.fire(HookEvent.POST_FINDING, event.finding.model_dump())
        return final

    # ------------------------------------------------------------------
    # Live event rendering
    # ------------------------------------------------------------------

    def _render_event(self, event) -> None:
        t = event.type
        if t == "tool_use":
            self.console.print(
                f"  [dim cyan]{_SPIN}[/dim cyan]  [dim]{event.tool}[/dim]",
                highlight=False,
            )
        elif t == "tool_result":
            marker = f"[green]{_TICK}[/green]" if event.ok else f"[red]{_CROSS}[/red]"
            self.console.print(
                f"  {marker}  [dim]{event.tool}[/dim]",
                highlight=False,
            )
        elif t == "finding":
            f = event.finding
            color = _SEVERITY_COLOR.get(f.severity, "white")
            label = _SEVERITY_LABEL.get(f.severity, f.severity.upper()[:4])
            self.console.print(
                f"  [bold {color}]{label}[/bold {color}]"
                f"  [white]{f.file}[/white][dim]:{f.line}[/dim]"
                f"  {f.title}",
                highlight=False,
            )
        elif t == "assistant_text" and event.text.strip():
            self.console.print(f"  [dim]{event.text.strip()}[/dim]", highlight=False)
        elif t == "error":
            self.console.print(f"  [red]error:[/red] {event.message}", highlight=False)

    # ------------------------------------------------------------------
    # Payload materialisation
    # ------------------------------------------------------------------

    def _materialize(self, task) -> str | None:
        from review_agent.types import DiffTask, FilesTask, PRTask, RepoTask

        if isinstance(task, DiffTask):
            try:
                out = subprocess.run(
                    ["git", "-C", str(self.cwd), "--no-pager", "diff", "--no-color",
                     f"{task.base}..{task.head}"],
                    capture_output=True, text=True, check=True, timeout=30,
                )
                diff = out.stdout
                if not diff.strip():
                    return "(empty diff — nothing to review)"
                return f"### Diff `{task.base}..{task.head}`\n\n```diff\n{diff}\n```"
            except Exception as exc:
                self.console.print(f"  [red]git diff failed: {exc}[/red]")
                return None
        if isinstance(task, FilesTask):
            return (
                "Files to review:\n"
                + "\n".join(f"- `{p}`" for p in task.paths)
                + "\n\nUse `read_file` to fetch their contents."
            )
        if isinstance(task, RepoTask):
            return "Whole-repo review. Use `glob` to enumerate files and `read_file` to inspect."
        if isinstance(task, PRTask):
            return f"(Review PR #{task.number} — use pr_fetch tool to get the diff)"
        return None
