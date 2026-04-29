"""MCP stdio server for review-agent.

Implements the Model Context Protocol (MCP) over stdin/stdout so any
MCP-capable agent (Claude Code, etc.) can call review-agent as a tool server.

Protocol subset implemented:
  - initialize / initialized handshake
  - tools/list
  - tools/call

This is a minimal implementation using the raw JSON-RPC protocol;
it does not require the `mcp` Python SDK (which may not be available).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


class MCPServer:
    """Minimal MCP stdio server."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._request_id = 0
        self._last_findings: list[dict] = []

    def _send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _tool_list(self) -> list[dict]:
        return [
            {
                "name": "review_diff",
                "description": (
                    "Run the AI code review agent on a git diff range. "
                    "Returns a markdown report with findings."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "description": "Base ref (e.g. main)"},
                        "head": {"type": "string", "description": "Head ref (e.g. HEAD)"},
                        "reviewers": {
                            "type": "string",
                            "description": "Comma-separated reviewers, or 'all'",
                        },
                        "cwd": {"type": "string", "description": "Working directory"},
                    },
                    "required": [],
                },
            },
            {
                "name": "review_files",
                "description": "Run the AI code review agent on specific files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Repo-relative paths to review",
                        },
                        "reviewers": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["paths"],
                },
            },
            {
                "name": "review_pr",
                "description": "Review a GitHub pull request. Requires GITHUB_TOKEN.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "owner/repo"},
                        "number": {"type": "integer", "description": "PR number"},
                        "reviewers": {"type": "string"},
                    },
                    "required": ["repo", "number"],
                },
            },
            {
                "name": "get_findings",
                "description": "Return findings from the last review as a JSON array.",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
        ]

    async def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        import asyncio
        from review_agent.coordinator import ParallelCoordinator, build_default_registry
        from review_agent.findings.format import format_markdown
        from review_agent.llm.groq_client import GroqClient, config_from_env
        from review_agent.types import DiffTask, FilesTask, FinalEvent, PRTask

        if name == "get_findings":
            return json.dumps(self._last_findings, indent=2)

        cwd = Path(args.get("cwd", str(self.cwd)))
        reviewers_str = args.get("reviewers", "correctness")
        from review_agent.cli import ALL_REVIEWERS
        reviewer_list = (
            ALL_REVIEWERS if reviewers_str.strip().lower() == "all"
            else [r.strip() for r in reviewers_str.split(",") if r.strip()]
        )

        try:
            groq_config = config_from_env(model="llama-3.3-70b-versatile")
        except Exception as e:
            return f"ERROR: {e}"

        registry = build_default_registry()
        groq = GroqClient(groq_config)

        if name == "review_diff":
            base = args.get("base", "main")
            head = args.get("head", "HEAD")
            task = DiffTask(base=base, head=head, cwd=str(cwd))
        elif name == "review_files":
            task = FilesTask(paths=args["paths"], cwd=str(cwd))
        elif name == "review_pr":
            task = PRTask(repo=args["repo"], number=args["number"])
        else:
            return f"Unknown tool: {name}"

        payload = self._build_payload(task, cwd)
        coord = ParallelCoordinator(
            groq=groq,
            registry=registry,
            cwd=cwd,
            reviewer_names=reviewer_list,
        )

        final = None
        async for event in coord.review(task, payload):
            if isinstance(event, FinalEvent):
                final = event

        findings = final.findings if final else []
        self._last_findings = [f.model_dump(mode="json") for f in findings]
        return format_markdown(findings)

    def _build_payload(self, task, cwd: Path) -> str:
        import subprocess
        from review_agent.types import DiffTask, FilesTask, PRTask, RepoTask

        if isinstance(task, DiffTask):
            try:
                out = subprocess.run(
                    ["git", "-C", str(cwd), "--no-pager", "diff", "--no-color",
                     f"{task.base}..{task.head}"],
                    capture_output=True, text=True, check=True, timeout=30,
                )
                diff = out.stdout
                return f"### Diff\n\n```diff\n{diff}\n```" if diff.strip() else "(empty diff)"
            except Exception as e:
                return f"(could not get diff: {e})"
        if isinstance(task, FilesTask):
            return (
                "Files to review:\n"
                + "\n".join(f"- `{p}`" for p in task.paths)
                + "\n\nUse `read_file` to fetch their contents."
            )
        if isinstance(task, PRTask):
            return f"(PR review — use pr_fetch for {task.repo}#{task.number})"
        return "Whole-repo review. Use `glob` to list files."

    async def serve(self) -> None:
        """Read JSON-RPC messages from stdin, handle, write responses to stdout."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        transport, protocol = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
        )

        while True:
            try:
                line = await reader.readline()
            except Exception:
                break
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            await self._handle(msg)

    async def _handle(self, msg: dict) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")

        if method == "initialize":
            self._send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "review-agent", "version": "0.1.0"},
                },
            })
        elif method == "notifications/initialized":
            pass  # no response
        elif method == "tools/list":
            self._send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self._tool_list()},
            })
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result_text = await self._call_tool(tool_name, arguments)
                self._send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                        "isError": False,
                    },
                })
            except Exception as e:
                self._send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"ERROR: {e}"}],
                        "isError": True,
                    },
                })
        else:
            if req_id is not None:
                self._send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


def main() -> None:
    cwd = Path(os.environ.get("REVIEW_AGENT_CWD", Path.cwd()))
    server = MCPServer(cwd=cwd)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
