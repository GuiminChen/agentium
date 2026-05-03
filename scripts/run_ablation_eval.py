#!/usr/bin/env python3
"""Three-way ablation driver for paper evaluation (Full / No-manifest / Permissive).

Requires ``AGENTIUM_EVALUATION_ABLATION=1`` in child processes only; this script does
not export it for the parent shell.

Success criteria:

- ``full``: all ``pytest -m paper`` tests pass.
- ``no_manifest`` / ``permissive``: exactly the pytest node ids listed under
  ``expected_ablation_failures`` in ``configs/ablation/paper_scenarios.json`` must
  fail; every other ``@pytest.mark.paper`` test must pass.

Run from repository root::

    python scripts/run_ablation_eval.py

Outputs under ``artifacts/ablation_<UTC-timestamp>/`` unless ``--output-dir`` is set.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, FrozenSet, List, Set

_ROOT = Path(__file__).resolve().parents[1]


def _ensure_src() -> None:
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _load_expected(path: Path) -> Dict[str, List[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ef = data.get("expected_ablation_failures") or {}
    out: Dict[str, List[str]] = {}
    for k, v in ef.items():
        if isinstance(v, list):
            out[k] = [str(x) for x in v]
    return out


def _junit_case_to_pytest_nodeid(classname: str, name: str) -> str:
    """JUnit uses dotted modules; hypothesis mapping uses POSIX pytest node ids."""

    rel = classname.replace(".", "/") + ".py"
    return f"{rel}::{name}"


def _failure_nodeids(xml_path: Path) -> FrozenSet[str]:
    if not xml_path.is_file():
        return frozenset()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    out: List[str] = []
    for case in root.iter("testcase"):
        fname = case.attrib.get("classname", "") or ""
        name = case.attrib.get("name", "") or ""
        for child in case:
            if child.tag not in {"failure", "error"}:
                continue
            out.append(_junit_case_to_pytest_nodeid(fname, name))
    return frozenset(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios-json",
        type=Path,
        default=_ROOT / "configs" / "ablation" / "paper_scenarios.json",
        help="Path to hypothesis + expected failure mapping.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write outputs here (default: artifacts/ablation_<UTC>).",
    )
    parser.add_argument(
        "--skip-microbench",
        action="store_true",
        help="Do not run scripts/ablation_microbench.py after pytest matrix.",
    )
    args = parser.parse_args()

    _ensure_src()
    from agentium.evaluation.env_fingerprint import capture_env_fingerprint

    scenarios_path = args.scenarios_json.resolve()
    if not scenarios_path.is_file():
        print(f"missing scenarios file: {scenarios_path}", file=sys.stderr)
        return 2

    expected_by_variant = _load_expected(scenarios_path)
    utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_dir
    if out_dir is None:
        out_dir = _ROOT / "artifacts" / f"ablation_{utc}"
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    variants = ["full", "no_manifest", "permissive"]
    fp = capture_env_fingerprint(
        extras={
            "repro_script": "scripts/run_ablation_eval.py",
        }
    )
    fp_dict = dataclasses.asdict(fp)
    fp_dict.pop("packages", None)
    summary: Dict[str, object] = {
        "output_dir": str(out_dir.relative_to(_ROOT)),
        "scenarios_json": str(scenarios_path.relative_to(_ROOT)),
        "variants": {},
        "fingerprint": fp_dict,
    }

    for variant in variants:
        junit_path = out_dir / f"junit_{variant}.xml"
        env = dict(os.environ)
        env["AGENTIUM_EVALUATION_ABLATION"] = "1"
        env["AGENTIUM_ABLATION_VARIANT"] = variant
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(_ROOT / "tests"),
            "-m",
            "paper",
            "-q",
            "--tb=no",
            f"--junit-xml={junit_path}",
        ]
        proc = subprocess.run(cmd, cwd=_ROOT, env=env)
        failed = set(_failure_nodeids(junit_path))
        exp_raw = expected_by_variant.get(variant, [])
        expected: Set[str] = set(exp_raw) if variant != "full" else set()

        detail: str | None = None
        if variant == "full":
            variant_ok = proc.returncode == 0 and not failed
            if not variant_ok:
                detail = "full variant must pass all paper tests with empty failure set"
        else:
            variant_ok = failed == expected
            if not variant_ok:
                detail = (
                    f"failure set mismatch: got {sorted(failed)!r} expected {sorted(expected)!r}"
                )

        summary["variants"][variant] = {
            "pytest_exit_code": proc.returncode,
            "failed_nodeids": sorted(failed),
            "expected_failed_nodeids": sorted(expected),
            "ok": variant_ok,
            "junit_xml": str(junit_path.relative_to(_ROOT)),
            "detail": detail,
        }
        if not variant_ok:
            out_json = out_dir / "ablation_summary.json"
            out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary["variants"][variant], indent=2))
            print(detail or "variant failed", file=sys.stderr)
            return 3

    bench: Dict[str, object] | None = None
    if not args.skip_microbench:
        bench_path = out_dir / "microbench.json"
        r = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "ablation_microbench.py"), "--output", str(bench_path)],
            cwd=_ROOT,
        )
        if r.returncode != 0:
            summary["microbench_exit_code"] = r.returncode
            out_json = out_dir / "ablation_summary.json"
            out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            return r.returncode
        if bench_path.is_file():
            bench = json.loads(bench_path.read_text(encoding="utf-8"))
        summary["microbench"] = bench

    summary["status"] = "ok"
    out_json = out_dir / "ablation_summary.json"
    out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
