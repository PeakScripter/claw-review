"""Hook runner for the review agent.

Hooks fire at named lifecycle points. Each hook entry in settings.toml is a
shell command (run via subprocess) that receives the event payload on stdin
as JSON and can:

  - Exit 0         → allow / no-op
  - Exit non-zero  → block the action (for PreReview / PreToolUse)
  - Write to stdout → the output is shown in the CLI

Example settings.toml:

    [hooks]
    PostReview = ["./scripts/upload_report.sh"]
    PostFinding = ["./scripts/notify_slack.sh"]
"""

from review_agent.hooks.runner import HookRunner, HookEvent

__all__ = ["HookRunner", "HookEvent"]
