"""Built-in tools for Agent Skills (materialize SKILL.md, optional allowlisted scripts)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from agentium.app.settings import AppSettings
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import DecisionType
from agentium.shared.errors import PolicyDeniedError
from agentium.shared.request_context import get_request_context
from agentium.skills.catalog import iter_skill_roots, load_merged_skill_manifests
from agentium.skills.manifest import SkillManifest, skill_markdown_body
from agentium.skills.routing import rank_skills_for_query
from agentium.sandbox.safety_sandbox import SafetySandbox, SandboxRequest
from agentium.tools.tool_registry import ToolRegistry, ToolSpec

ALLOWLIST_FILENAME = "agentium_script_allowlist.txt"
_DEFAULT_BODY_CHARS = 120_000


def _normalize_relpath(raw: str) -> str:
    return raw.replace("\\", "/").strip().lstrip("./")


def _pack_script_allowlist(skill_dir: Path) -> set[str]:
    path = skill_dir / ALLOWLIST_FILENAME
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(_normalize_relpath(s))
    return out


def _resolved_script_path(skill_dir: Path, relpath: str) -> Path:
    skill_base = skill_dir.resolve()
    n = _normalize_relpath(relpath)
    candidate = (skill_dir / n).resolve()
    if not candidate.is_relative_to(skill_base):
        raise ValueError("script path escapes skill directory")
    return candidate


def _enforce_skill_policy(policy: PolicyEngine, skill_id: str, label: str) -> None:
    ctx = get_request_context()
    d = policy.decide_skill_use(ctx, skill_id)
    if d.decision == DecisionType.DENY:
        raise PolicyDeniedError(d.reason)
    if d.decision == DecisionType.REQUIRE_APPROVAL:
        raise PolicyDeniedError(
            f"{label}: skill policy requires approval for skill {skill_id!r}; "
            "use an explicit allow rule for decide_skill_use in development."
        )


def _enforce_script_policy(policy: PolicyEngine, skill_id: str, relpath: str) -> None:
    ctx = get_request_context()
    d = policy.decide_skill_script(ctx, skill_id, relpath)
    if d.decision == DecisionType.DENY:
        raise PolicyDeniedError(d.reason)
    if d.decision == DecisionType.REQUIRE_APPROVAL:
        raise PolicyDeniedError(
            f"skill script policy requires approval for {skill_id!r} {relpath!r}; "
            "narrow rules or use development policy."
        )


def _make_skill_run_handler(settings: AppSettings, policy_engine: PolicyEngine):
    def _skill_run(args: Dict[str, Any]) -> Dict[str, Any]:
        q = str(args.get("query", "")).strip()
        if not q:
            return {"error": "missing_query", "ok": False}
        max_raw = args.get("max_body_chars")
        max_chars = _DEFAULT_BODY_CHARS if max_raw is None else int(max_raw)
        manifests = load_merged_skill_manifests(settings)
        roots = [str(p) for p in iter_skill_roots(settings)]
        if not manifests:
            return {
                "error": "no_skills_configured",
                "ok": False,
                "roots_tried": roots,
            }
        ranked = rank_skills_for_query(q, manifests, top_n=12)
        if not ranked or ranked[0][1] <= 0.0:
            return {
                "error": "no_skill_match",
                "ok": False,
                "query": q,
                "ranked": [(m.name, float(s)) for m, s in ranked[:8]],
                "roots_tried": roots,
            }
        primary, score = ranked[0]
        _enforce_skill_policy(policy_engine, primary.name, "skill_run")

        body = skill_markdown_body(primary.skill_md_path)
        truncated = False
        if max_chars > 0 and len(body) > max_chars:
            body = body[:max_chars]
            truncated = True

        return {
            "ok": True,
            "primary_skill_id": primary.name,
            "primary_score": float(score),
            "ranked": [(m.name, float(s)) for m, s in ranked[:12]],
            "skill_body": body,
            "skill_body_truncated": truncated,
            "skill_md_path": str(primary.skill_md_path),
            "roots_tried": roots,
        }

    return _skill_run


def _make_skill_invoke_handler(settings: AppSettings, policy_engine: PolicyEngine, sandbox: SafetySandbox):
    def _skill_invoke(args: Dict[str, Any]) -> Dict[str, Any]:
        skill_id = str(args.get("skill_id", "")).strip()
        script_raw = str(args.get("script", "")).strip()
        if not skill_id or not script_raw:
            return {"error": "skill_id_and_script_required", "ok": False}

        argv_in = args.get("script_argv") or []
        if not isinstance(argv_in, list):
            return {"error": "script_argv_must_be_list", "ok": False}
        argv: List[str] = [str(x) for x in argv_in]

        timeout = float(args.get("timeout_seconds", 60.0))
        timeout = max(1.0, min(timeout, 120.0))

        by_name: Dict[str, SkillManifest] = {m.name: m for m in load_merged_skill_manifests(settings)}
        if skill_id not in by_name:
            return {"error": "unknown_skill", "skill_id": skill_id, "ok": False}

        man = by_name[skill_id]
        npath = _normalize_relpath(script_raw)

        pack_allow = _pack_script_allowlist(man.skill_dir)
        if not pack_allow:
            return {
                "error": "no_pack_allowlist",
                "ok": False,
                "hint": f"add {ALLOWLIST_FILENAME} under the skill directory with allowed relative paths",
            }
        if npath not in pack_allow:
            return {
                "error": "script_not_allowlisted_in_pack",
                "script": npath,
                "ok": False,
            }

        try:
            script_path = _resolved_script_path(man.skill_dir, script_raw)
        except ValueError as exc:
            return {"error": "invalid_script_path", "detail": str(exc), "ok": False}

        if not script_path.is_file():
            return {"error": "script_not_found", "path": str(script_path), "ok": False}

        _enforce_skill_policy(policy_engine, skill_id, "skill_invoke")
        _enforce_script_policy(policy_engine, skill_id, npath)

        ctx = get_request_context()

        def _subprocess() -> Dict[str, Any]:
            proc = subprocess.run(
                [sys.executable, str(script_path), *argv],
                cwd=str(man.skill_dir.resolve()),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out_limit = 64_000
            stdout = (proc.stdout or "")[:out_limit]
            stderr = (proc.stderr or "")[:out_limit]
            return {
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": len(proc.stdout or "") > out_limit,
                "stderr_truncated": len(proc.stderr or "") > out_limit,
            }

        outcome = sandbox.run(
        SandboxRequest(
            tool_name="skill_invoke",
            tenant_id=ctx.tenant_id,
            capabilities=["skill.subprocess"],
            run_id=ctx.run_id,
        ),
            _subprocess,
        )
        payload = outcome.output if isinstance(outcome.output, dict) else {"raw": outcome.output}
        return {
            "ok": True,
            "skill_id": skill_id,
            "script": npath,
            "duration_ms": outcome.duration_ms,
            **payload,
        }

    return _skill_invoke


def register_skill_tools(
    tool_registry: ToolRegistry,
    settings: AppSettings,
    policy_engine: PolicyEngine,
    safety_sandbox: SafetySandbox,
) -> None:
    """Register ``skill_run`` and ``skill_invoke`` (requires sandbox profiles for invoke)."""

    tool_registry.register(
        ToolSpec(
            name="skill_run",
            capabilities=["skills", "skills.materialize"],
            risk_level="medium",
            handler=_make_skill_run_handler(settings, policy_engine),
        )
    )
    tool_registry.register(
        ToolSpec(
            name="skill_invoke",
            capabilities=["skills", "skills.subprocess"],
            risk_level="high",
            handler=_make_skill_invoke_handler(settings, policy_engine, safety_sandbox),
        )
    )
