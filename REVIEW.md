# Review Charter

This file is the project's review charter. It is loaded into the system prompt on every review and tells the agent what to flag, what to ignore, and what conventions are in force.

## What to flag

- Logic bugs: off-by-one, wrong operator, mishandled None/empty cases, swapped arguments.
- Concurrency issues: races on shared mutable state, missing `await`, blocking calls in async paths.
- Security: command injection, path traversal, unsafe deserialization, hardcoded secrets.
- Read-only invariant breaks: any new tool whose `is_read_only` ClassVar is not literal `True`, any `subprocess.run(..., shell=True)`, any new `open(path, "w"|"a")` outside `findings/format/` or test fixtures.
- Missing tests for new tools or new public functions.

## What to ignore

- Style issues already enforced by ruff (don't duplicate the linter).
- Type annotations on private helpers — type the public surface.
- Speculative future-proofing (premature abstractions, unused parameters "for later").

## Conventions

- Python 3.11+; use `match`, `Self`, PEP 604 unions, `ExceptionGroup` where natural.
- Pydantic v2 for all schemas crossing a boundary (LLM, CLI, config).
- All tools subprocess analyzers with hardcoded argv lists; never `shell=True`.
- Findings always carry `file`, `line`, `severity`, `rationale`. Suggestions are text only — never a patch.
