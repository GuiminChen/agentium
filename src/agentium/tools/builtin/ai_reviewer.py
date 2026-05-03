"""AI code review service implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agentium.models.review import CodeFile
from agentium.shared.errors import ConfigurationError

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - validated via runtime config checks
    AsyncOpenAI = None

LOGGER = logging.getLogger(__name__)


class AIReviewer:
    """Run AI-assisted code review against changed files."""

    def __init__(self, api_key: str, model: str = "gpt-4-turbo-preview") -> None:
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for AI review")
        if AsyncOpenAI is None:
            raise ConfigurationError(
                "openai package is required. Install with `pip install -e '.[openai]'`."
            )
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.project_rules = self._load_project_rules()

    def _load_project_rules(self) -> str:
        """Load project rules for model context."""
        rules_files = [
            ".cursorrules",
            ".cursor/rules/architecture.md",
            ".cursor/rules/python_coding.md",
            ".cursor/rules/agent_design.md",
            ".cursor/rules/project_structure.mdc",
        ]
        rules: list[str] = []
        for file_path in rules_files:
            path = Path(file_path)
            if path.exists():
                rules.append(f"=== {file_path} ===\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(rules)

    async def review_file(self, code_file: CodeFile) -> dict[str, Any]:
        """Review a single file and return structured findings."""
        prompt = f"""
你是一个资深的Python架构师，正在审查Agentium项目的代码。

## 项目规范
{self.project_rules}

## 需要审查的代码
文件路径：{code_file.path}

代码内容：
```python
{code_file.content}
```

变更内容（git diff）：
```
{code_file.changes}
```

请以JSON格式返回审查结果：
{{
  "file": "文件名",
  "issues": [
    {{
      "type": "architecture|code_quality|security|performance|testability|documentation",
      "description": "问题描述",
      "severity": "high|medium|low",
      "suggestion": "修复建议",
      "example": "示例代码（可选）"
    }}
  ],
  "overall_score": 0-100,
  "summary": "总体评价"
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是Agentium项目的首席架构师，负责代码质量审查。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            LOGGER.exception("AI review failed for %s", code_file.path)
            return {
                "file": str(code_file.path),
                "issues": [
                    {
                        "type": "system_error",
                        "description": f"AI审查失败: {exc}",
                        "severity": "high",
                        "suggestion": "请人工审查此文件",
                    }
                ],
                "overall_score": 0,
                "summary": "审查过程中发生错误",
            }

    async def generate_summary(self, reviews: list[dict[str, Any]]) -> str:
        """Generate markdown summary from review records."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是技术负责人，正在生成代码审查报告。"},
                {
                    "role": "user",
                    "content": (
                        "基于以下文件审查结果，生成全面总结（总体评分、问题分类统计、"
                        "高风险问题、改进建议、是否建议合并）：\n\n"
                        f"{json.dumps(reviews, indent=2, ensure_ascii=False)}"
                    ),
                },
            ],
            temperature=0.1,
        )
        return response.choices[0].message.content
