"""PRFetchTool — fetch pull request metadata and file diffs from GitHub.

Uses the GitHub REST API via httpx.  Requires GITHUB_TOKEN in the environment.
The agent has NO write surface to GitHub — this tool is strictly read-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult

_GITHUB_API = "https://api.github.com"
_MAX_FILES = 300
_MAX_PATCH_BYTES = 800_000


class PRFetchInput(BaseModel):
    repo: str = Field(description="GitHub repo in `owner/repo` format.")
    number: int = Field(ge=1, description="Pull request number.")
    include_patch: bool = Field(
        default=True, description="Include the unified diff patch for each file."
    )


@dataclass
class PRFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


@dataclass
class PRInfo:
    number: int
    title: str
    body: str
    base_ref: str
    head_ref: str
    head_sha: str
    author: str
    files: list[PRFile]


class PRFetchTool(ReviewTool[PRFetchInput, PRInfo]):
    name: ClassVar[str] = "pr_fetch"
    description: ClassVar[str] = (
        "Fetch pull request metadata and per-file diffs from GitHub. "
        "Returns the PR title, description, base/head refs, and unified diffs. "
        "Requires GITHUB_TOKEN environment variable."
    )
    input_model: ClassVar[type[BaseModel]] = PRFetchInput
    is_read_only: ClassVar = True

    async def call(self, input: PRFetchInput, ctx: ToolContext) -> ToolResult[PRInfo]:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return ToolResult(
                ok=False,
                summary="GITHUB_TOKEN environment variable is not set.",
                error="no_token",
            )

        try:
            import httpx
        except ImportError:
            return ToolResult(
                ok=False,
                summary="`httpx` is required for pr_fetch. Install with `pip install httpx`.",
                error="no_httpx",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                pr_resp = await client.get(
                    f"{_GITHUB_API}/repos/{input.repo}/pulls/{input.number}"
                )
                pr_resp.raise_for_status()
                pr = pr_resp.json()

                files_resp = await client.get(
                    f"{_GITHUB_API}/repos/{input.repo}/pulls/{input.number}/files",
                    params={"per_page": _MAX_FILES},
                )
                files_resp.raise_for_status()
                raw_files = files_resp.json()
        except Exception as e:
            return ToolResult(ok=False, summary=f"GitHub API error: {e}", error=str(e))

        files: list[PRFile] = []
        total_patch_bytes = 0
        for rf in raw_files[:_MAX_FILES]:
            patch = rf.get("patch", "") or ""
            if total_patch_bytes + len(patch.encode()) > _MAX_PATCH_BYTES:
                patch = f"(patch omitted — exceeds {_MAX_PATCH_BYTES // 1024}KB limit)"
            else:
                total_patch_bytes += len(patch.encode())
            files.append(
                PRFile(
                    filename=rf["filename"],
                    status=rf["status"],
                    additions=rf.get("additions", 0),
                    deletions=rf.get("deletions", 0),
                    patch=patch,
                )
            )

        info = PRInfo(
            number=input.number,
            title=pr.get("title", ""),
            body=pr.get("body", "") or "",
            base_ref=pr["base"]["ref"],
            head_ref=pr["head"]["ref"],
            head_sha=pr["head"]["sha"],
            author=pr["user"]["login"],
            files=files,
        )

        # Build the summary the LLM will see.
        summary_lines = [
            f"PR #{info.number}: {info.title}",
            f"Author: {info.author} | {info.base_ref} ← {info.head_ref}",
            f"Files changed: {len(files)}",
            "",
        ]
        for f in files:
            summary_lines.append(
                f"  {f.status:8s} +{f.additions}/-{f.deletions}  {f.filename}"
            )
            if f.patch and input.include_patch:
                summary_lines.append("```diff")
                summary_lines.append(f.patch[:4000])
                if len(f.patch) > 4000:
                    summary_lines.append("  …[truncated]")
                summary_lines.append("```")
                summary_lines.append("")

        return ToolResult(ok=True, summary="\n".join(summary_lines), data=info)
