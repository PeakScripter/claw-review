"""Built-in slash commands for the review REPL.

Commands:
  /findings          List all findings in the current review
  /explain <id>      Ask the agent to justify a finding in depth
  /ignore <id> why   Mark a finding as accepted/false-positive
  /export <fmt>      Print the findings in a given format
  /help              List all commands
  /cost              Show token usage (if available)
  /clear             Clear terminal
"""

from __future__ import annotations

from review_agent.commands.registry import Command, CommandRegistry


def register_builtin_commands(registry: CommandRegistry) -> None:
    registry.register(Command(
        name="help",
        description="List available slash commands",
        usage="/help",
        handler=_help(registry),
    ))
    registry.register(Command(
        name="findings",
        description="List all findings in the current review",
        usage="/findings [severity]",
        handler=_findings,
    ))
    registry.register(Command(
        name="explain",
        description="Ask the agent to elaborate on a finding",
        usage="/explain <finding-id>",
        handler=_explain,
    ))
    registry.register(Command(
        name="ignore",
        description="Mark a finding as accepted/false-positive",
        usage='/ignore <finding-id> "reason"',
        handler=_ignore,
    ))
    registry.register(Command(
        name="export",
        description="Print findings in a given format",
        usage="/export <markdown|json|sarif|github>",
        handler=_export,
    ))
    registry.register(Command(
        name="cost",
        description="Show token usage for this session",
        usage="/cost",
        handler=_cost,
    ))
    registry.register(Command(
        name="clear",
        description="Clear the terminal",
        usage="/clear",
        handler=_clear,
    ))


def _help(registry: CommandRegistry):
    def handler(args, session):
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 4, 0, 0), expand=False)
        table.add_column("usage", style="cyan", no_wrap=True, min_width=38)
        table.add_column("desc", style="dim")
        for cmd in registry.all():
            table.add_row(cmd.usage, cmd.description)
        return table
    return handler


def _findings(args: str, session) -> str:
    from review_agent.findings.format.markdown import format_markdown
    findings = session.findings.snapshot() if session and hasattr(session, "findings") else []
    if args.strip():
        sev_filter = args.strip().lower()
        findings = [f for f in findings if f.severity == sev_filter]
    if not findings:
        return "No findings yet." if not args.strip() else f"No {args.strip()} findings."
    return format_markdown(findings)


def _explain(args: str, session) -> str:
    finding_id = args.strip()
    if not finding_id:
        return "Usage: /explain <finding-id>"
    findings = session.findings.snapshot() if session and hasattr(session, "findings") else []
    match = next((f for f in findings if f.id == finding_id), None)
    if not match:
        return f"Finding {finding_id!r} not found. Use /findings to list IDs."
    return (
        f"Finding **{match.id}** — {match.title}\n\n"
        f"**File:** `{match.file}:{match.line}`\n"
        f"**Severity:** {match.severity}  **Category:** {match.category}\n\n"
        f"**Rationale:**\n{match.rationale}\n\n"
        + (f"**Suggestion:**\n{match.suggestion}\n" if match.suggestion else "")
        + (f"**References:**\n" + "\n".join(f"- {r}" for r in match.references) if match.references else "")
    )


def _ignore(args: str, session) -> str:
    import shlex
    parts = shlex.split(args) if args.strip() else []
    if len(parts) < 2:
        return 'Usage: /ignore <finding-id> "reason why this is acceptable"'
    finding_id = parts[0]
    reason = " ".join(parts[1:])
    findings = session.findings.snapshot() if session and hasattr(session, "findings") else []
    match = next((f for f in findings if f.id == finding_id), None)
    if not match:
        return f"Finding {finding_id!r} not found."

    # Append to .review/ignore.yaml in the working directory.
    if session and hasattr(session, "cwd"):
        ignore_path = session.cwd / ".review" / "ignore.yaml"
        ignore_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        existing = []
        if ignore_path.is_file():
            try:
                existing = yaml.safe_load(ignore_path.read_text()) or []
            except Exception:
                existing = []
        existing.append({
            "id": finding_id,
            "file": match.file,
            "line": match.line,
            "title": match.title,
            "reason": reason,
        })
        ignore_path.write_text(yaml.dump(existing, allow_unicode=True))
        return f"Finding {finding_id} marked as ignored. Written to .review/ignore.yaml."
    return f"Finding {finding_id} would be ignored (no cwd available to write ignore file)."


def _export(args: str, session) -> str:
    from review_agent.findings.format import (
        format_github,
        format_json,
        format_markdown,
        format_sarif,
    )
    fmt = args.strip().lower() or "markdown"
    findings = session.findings.snapshot() if session and hasattr(session, "findings") else []
    if fmt == "markdown":
        return format_markdown(findings)
    if fmt == "json":
        return format_json(findings)
    if fmt == "sarif":
        return format_sarif(findings)
    if fmt == "github":
        return format_github(findings)
    return f"Unknown format: {fmt!r}. Choose: markdown, json, sarif, github"


def _cost(args: str, session) -> str:
    if session and hasattr(session, "token_usage"):
        u = session.token_usage
        return (
            f"Token usage:\n"
            f"  Input:  {u.get('input_tokens', 0):,}\n"
            f"  Output: {u.get('output_tokens', 0):,}\n"
            f"  Total:  {u.get('total', 0):,}"
        )
    return "Token usage not available."


def _clear(args: str, session) -> str:
    import os
    os.system("clear" if os.name != "nt" else "cls")
    return ""
