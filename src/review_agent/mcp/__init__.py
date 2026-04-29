"""MCP server — exposes review tools to other agents via the Model Context Protocol.

Usage (as an MCP server called from Claude Code or another agent):

    # In .claude/settings.json:
    {
      "mcpServers": {
        "review-agent": {
          "command": "python",
          "args": ["-m", "review_agent.mcp"]
        }
      }
    }

The server exposes these tools:
  review_diff   — review a git diff range
  review_files  — review specific files
  review_pr     — review a GitHub pull request
  get_findings  — retrieve findings from the last review as JSON
"""
