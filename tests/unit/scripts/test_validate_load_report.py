from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_validator(tmp_path: Path, report_payload: dict) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report_payload), encoding="utf-8")
    script_path = _REPO_ROOT / "scripts" / "validate_load_report.py"
    baseline_path = _REPO_ROOT / "docs" / "security-ops" / "load-test-baseline.yaml"
    return subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--baseline",
            str(baseline_path),
            "--report",
            str(report_path),
            "--format",
            "json",
        ],
        cwd=str(_REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def test_validator_passes_with_valid_report(tmp_path: Path) -> None:
    report = {
        "metrics": {
            "manifest_violation_total": 10,
            "unauthorized_tool_call_block_total": 10,
            "dlp_block_total": 297,
            "secret_pattern_hit_rate": 0.97,
            "cross_tenant_block_total": 500,
            "approval_bypass_attempt_total": 20,
            "approval_bypass_success_total": 0,
            "background_false_trigger_rate": 0.02,
            "background_killswitch_count": 0,
            "recovery_mttr_seconds": 240,
            "safe_degrade_success_rate": 1.0,
            "sensitive_total": 300,
            "non_sensitive_total": 700,
            "dlp_false_positive_total": 18,
            "cross_tenant_attempt_total": 500,
            "background_false_trigger_window_minutes": 10,
        }
    }
    result = _run_validator(tmp_path, report)
    assert result.returncode == 0, result.stdout + result.stderr
    body = json.loads(result.stdout)
    assert body["pass"] is True


def test_validator_fails_on_threshold_violation(tmp_path: Path) -> None:
    report = {
        "metrics": {
            "manifest_violation_total": 10,
            "unauthorized_tool_call_block_total": 8,
            "dlp_block_total": 280,
            "secret_pattern_hit_rate": 0.93,
            "cross_tenant_block_total": 490,
            "approval_bypass_attempt_total": 10,
            "approval_bypass_success_total": 1,
            "background_false_trigger_rate": 0.02,
            "background_killswitch_count": 0,
            "recovery_mttr_seconds": 420,
            "safe_degrade_success_rate": 0.8,
            "sensitive_total": 300,
            "non_sensitive_total": 700,
            "dlp_false_positive_total": 40,
            "cross_tenant_attempt_total": 500,
            "background_false_trigger_window_minutes": 10,
        }
    }
    result = _run_validator(tmp_path, report)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["pass"] is False
    assert any("mttr_seconds_max" in reason for reason in body["reasons"])


def test_validator_requires_killswitch_on_high_false_trigger(tmp_path: Path) -> None:
    report = {
        "metrics": {
            "manifest_violation_total": 10,
            "unauthorized_tool_call_block_total": 10,
            "dlp_block_total": 300,
            "secret_pattern_hit_rate": 0.98,
            "cross_tenant_block_total": 500,
            "approval_bypass_attempt_total": 10,
            "approval_bypass_success_total": 0,
            "background_false_trigger_rate": 0.08,
            "background_killswitch_count": 0,
            "recovery_mttr_seconds": 200,
            "safe_degrade_success_rate": 1.0,
            "sensitive_total": 300,
            "non_sensitive_total": 700,
            "dlp_false_positive_total": 5,
            "cross_tenant_attempt_total": 500,
            "background_false_trigger_window_minutes": 10,
        }
    }
    result = _run_validator(tmp_path, report)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert any("background_killswitch_count" in reason for reason in body["reasons"])
