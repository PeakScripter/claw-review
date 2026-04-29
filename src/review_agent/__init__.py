"""review-agent — read-only AI code review agent.

Public SDK surface:

    from review_agent import ReviewEngine, ParallelCoordinator
    from review_agent import DiffTask, FilesTask, PRTask, RepoTask
    from review_agent import FindingStore, Finding
    from review_agent import format_markdown, format_json, format_sarif, format_github
    from review_agent.coordinator import build_default_registry
    from review_agent.llm.groq_client import GroqClient, config_from_env

Example (headless, Python API):

    import asyncio
    from review_agent import ReviewEngine, DiffTask, format_markdown
    from review_agent.coordinator import ParallelCoordinator, build_default_registry
    from review_agent.llm.groq_client import GroqClient, config_from_env

    async def main():
        groq = GroqClient(config_from_env(model="llama-3.3-70b-versatile"))
        registry = build_default_registry()
        coord = ParallelCoordinator(
            groq=groq,
            registry=registry,
            cwd=Path("."),
            reviewer_names=["correctness", "security"],
        )
        task = DiffTask(base="main", head="HEAD")
        payload = "..."  # your diff text
        async for event in coord.review(task, payload):
            if event.type == "final":
                print(format_markdown(event.findings))

    asyncio.run(main())
"""

__version__ = "0.1.0"

from review_agent.coordinator import ParallelCoordinator  # noqa: F401
from review_agent.engine import ReviewEngine  # noqa: F401
from review_agent.findings.format import (  # noqa: F401
    format_github,
    format_json,
    format_markdown,
    format_sarif,
)
from review_agent.findings.model import Finding  # noqa: F401
from review_agent.findings.store import FindingStore  # noqa: F401
from review_agent.types import DiffTask, FilesTask, FinalEvent, PRTask, RepoTask  # noqa: F401
