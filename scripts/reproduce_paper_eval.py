#!/usr/bin/env python3
"""Paper / open-source reproducibility entrypoint: fingerprint, paper tests, release gates.

Run from repository root::

    python scripts/reproduce_paper_eval.py

Exit code 0 only if all steps succeed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _ensure_src() -> None:
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _ensure_src()
    from agentium.evaluation.env_fingerprint import capture_env_fingerprint

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="Run pytest paper tests only (skip scripts/run_release_gates.py).",
    )
    parser.add_argument(
        "--skip-profiles",
        action="store_true",
        help="Skip scripts/run_paper_governance_profiles.py.",
    )
    args = parser.parse_args()

    artifacts = _ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)

    from agentium.app.plugins_config import load_plugins_config, plugins_fingerprint_payload

    plugins_path = Path(
        os.environ.get(
            "AGENTIUM_PLUGINS_CONFIG",
            str(_ROOT / "configs" / "runtime_plugins.default.yaml"),
        )
    ).resolve()
    plugins_fp = plugins_fingerprint_payload(load_plugins_config(plugins_path))

    fp = capture_env_fingerprint(
        extras={
            "repro_script": "scripts/reproduce_paper_eval.py",
            "root": str(_ROOT),
            "plugins_config_path": str(plugins_path),
            "plugins_runtime": json.dumps(plugins_fp, sort_keys=True, ensure_ascii=False),
        }
    )
    fp_path = artifacts / "paper_eval_fingerprint.json"
    fp_dict = dataclasses.asdict(fp)
    fp_path.write_text(json.dumps(fp_dict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary: dict = {
        "fingerprint_file": str(fp_path.relative_to(_ROOT)),
        "git_revision": fp.git_revision,
        "git_dirty": fp.git_dirty,
        "python_version": fp.python_version,
    }

    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(_ROOT / "tests"),
        "-m",
        "paper",
        "-q",
        "--tb=line",
    ]
    r1 = subprocess.run(pytest_cmd, cwd=_ROOT)
    summary["pytest_paper_exit_code"] = r1.returncode
    if r1.returncode != 0:
        out = artifacts / "paper_repro_summary.json"
        out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return r1.returncode

    if not args.skip_profiles:
        r_profiles = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "run_paper_governance_profiles.py")],
            cwd=_ROOT,
        )
        summary["governance_profiles_exit_code"] = r_profiles.returncode
        if r_profiles.returncode != 0:
            out = artifacts / "paper_repro_summary.json"
            out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return r_profiles.returncode

    if not args.skip_gates:
        r2 = subprocess.run([sys.executable, str(_ROOT / "scripts" / "run_release_gates.py")], cwd=_ROOT)
        summary["release_gates_exit_code"] = r2.returncode
        if r2.returncode != 0:
            out = artifacts / "paper_repro_summary.json"
            out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return r2.returncode

    out = artifacts / "paper_repro_summary.json"
    summary["status"] = "ok"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
