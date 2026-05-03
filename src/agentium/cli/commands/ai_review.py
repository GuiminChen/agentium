"""CLI command for AI code review."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Sequence

from agentium.models.review import CodeFile
from agentium.tools.builtin.ai_reviewer import AIReviewer


def build_parser() -> argparse.ArgumentParser:
    """Build parser for `ai-review` command."""
    parser = argparse.ArgumentParser(description="AI代码审查")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--repo", type=str, required=True)
    parser.add_argument("--author", type=str, required=True)
    return parser


def _sample_code_file() -> CodeFile:
    """Return sample payload until GitHub diff integration is implemented."""
    return CodeFile(
        path=Path("src/agentium/core/scheduler.py"),
        content=(
            "from typing import List\n"
            "class Task:\n"
            "    def __init__(self, task_id: str, priority: int) -> None:\n"
            "        self.id = task_id\n"
            "        self.priority = priority\n"
        ),
        changes="+ class Scheduler: ...",
    )


async def _run_review() -> int:
    """Run review flow and write markdown summary."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    reviewer = AIReviewer(api_key=api_key)
    review = await reviewer.review_file(_sample_code_file())
    summary = await reviewer.generate_summary([review])
    Path("ai_review_summary.md").write_text(summary, encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command entrypoint for AI review."""
    parser = build_parser()
    parser.parse_args(argv)
    return asyncio.run(_run_review())
