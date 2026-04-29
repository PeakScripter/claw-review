"""Built-in read-only tools."""

from review_agent.tools.add_finding import AddFindingTool
from review_agent.tools.dep_audit import DepAuditTool
from review_agent.tools.git_diff import GitDiffTool
from review_agent.tools.glob_tool import GlobTool
from review_agent.tools.grep import GrepTool
from review_agent.tools.lint import LintTool
from review_agent.tools.read_file import ReadFileTool
from review_agent.tools.retract_finding import RetractFindingTool
from review_agent.tools.sast import SASTTool
from review_agent.tools.test_run import TestRunTool
from review_agent.tools.type_check import TypeCheckTool

__all__ = [
    "AddFindingTool",
    "DepAuditTool",
    "GitDiffTool",
    "GlobTool",
    "GrepTool",
    "LintTool",
    "ReadFileTool",
    "RetractFindingTool",
    "SASTTool",
    "TestRunTool",
    "TypeCheckTool",
]

# Phase 1 tools — available to all reviewers by default.
PHASE1_TOOLS = ["read_file", "grep", "glob", "git_diff", "add_finding", "retract_finding"]

# Phase 3 analyzer tools — opt-in per reviewer manifest.
PHASE3_TOOLS = ["lint", "type_check", "sast", "dep_audit", "test_run"]

ALL_TOOL_NAMES = PHASE1_TOOLS + PHASE3_TOOLS


def default_tools() -> list:
    """Return one instance of every built-in read-only tool."""
    return [
        ReadFileTool(),
        GrepTool(),
        GlobTool(),
        GitDiffTool(),
        AddFindingTool(),
        RetractFindingTool(),
        LintTool(),
        TypeCheckTool(),
        SASTTool(),
        DepAuditTool(),
        TestRunTool(),
    ]
