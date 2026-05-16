"""Table 4 DSML tool-call documentation injected into system prompts (verbatim skeleton)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

# Report Table 4 — fixed prose around schema (tool definitions appended separately).
_DSML_SCHEMA_PREAMBLE = """## Tools
You have access to a set of tools to help answer the user's question. You can
invoke tools by writing a "<|DSML|tool_calls>" block like the following:
<|DSML|tool_calls>
<|DSML|invoke name="$TOOL_NAME">
<|DSML|parameter name="$PARAMETER_NAME" string="true|false">$PARAMETER_VALUE
</|DSML|parameter>
...
</|DSML|invoke>
<|DSML|invoke name="$TOOL_NAME2">
...
</|DSML|invoke>
</|DSML|tool_calls>
String parameters should be specified as is and set 'string="true"'. For all
other types (numbers, booleans, arrays, objects), pass the value in JSON
format and set 'string="false"'.
If thinking_mode is enabled (triggered by <think>), you MUST output your
complete reasoning inside <think>...</think> BEFORE any tool calls or
final response.
Otherwise, output directly after </think> with tool calls or final response.
### Available Tool Schemas
"""


_DSML_BLOCK_RE = re.compile(
    r"<\|DSML\|tool_calls>(.*?)</\|DSML\|tool_calls>",
    re.DOTALL | re.IGNORECASE,
)
_INVOKE_RE = re.compile(
    r"<\|DSML\|invoke\s+name=\"([^\"]+)\">(.*?)</\|DSML\|invoke>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_RE = re.compile(
    r"<\|DSML\|parameter\s+name=\"([^\"]+)\"\s+string=\"(true|false)\">(.*?)</\|DSML\|parameter>",
    re.DOTALL | re.IGNORECASE,
)


def build_dsml_tool_system_addon(tool_definitions_markdown: str) -> str:
    """Return full system appendix describing DSML plus formatted tool schemas."""

    body = tool_definitions_markdown.strip()
    return _DSML_SCHEMA_PREAMBLE + body + "\n\nYou MUST strictly follow the above defined tool name and parameter schemas to invoke tool calls.\n"


def format_tool_definitions_markdown(specs: Sequence[Dict[str, Any]]) -> str:
    """Human-readable tool listing for the DSML appendix (JSON schemas embedded)."""

    parts: List[str] = []
    for spec in specs:
        name = str(spec.get("name", "")).strip()
        if not name:
            continue
        desc = str(spec.get("description", "")).strip()
        schema = spec.get("parameters")
        if schema is None:
            schema = {"type": "object", "additionalProperties": True}
        parts.append(f"#### `{name}`\n{desc}\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```\n")
    return "\n".join(parts).strip()


def extract_dsml_tool_block(message_text: str) -> Optional[str]:
    """Return inner XML-ish payload of the DSML tool_calls block, if present."""

    if not message_text or "<|DSML|tool_calls>" not in message_text:
        return None
    m = _DSML_BLOCK_RE.search(message_text)
    if not m:
        return None
    return m.group(1).strip()


def dsml_tool_calls_to_openai_tool_calls(dsml_inner: str) -> List[Dict[str, Any]]:
    """Translate DSML invokes into OpenAI ``tool_calls`` items for execution."""

    out: List[Dict[str, Any]] = []
    for inv in _INVOKE_RE.finditer(dsml_inner):
        tool_name = inv.group(1).strip()
        inner = inv.group(2)
        args: Dict[str, Any] = {}
        for pm in _PARAM_RE.finditer(inner):
            pname = pm.group(1).strip()
            as_string = pm.group(2).lower() == "true"
            raw_val = pm.group(3).strip()
            if as_string:
                args[pname] = raw_val
            else:
                try:
                    args[pname] = json.loads(raw_val)
                except json.JSONDecodeError:
                    args[pname] = raw_val
        tc_id = f"call_dsml_{uuid.uuid4().hex[:24]}"
        out.append(
            {
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return out
