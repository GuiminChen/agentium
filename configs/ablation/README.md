# Ablation configs (paper evaluation only)

These files document the **three-way ablation** used for the paper’s controlled
experiments (Full / No-manifest / Permissive). They do **not** change runtime
behavior by themselves.

## Environment switches (default: off)

| Variable | Values | Meaning |
|----------|--------|---------|
| `AGENTIUM_EVALUATION_ABLATION` | unset / `false` | **Production default.** All ablation hooks disabled. |
| | `true` \| `1` \| `yes` \| `on` | Enable evaluation hooks (still requires explicit variant below). |
| `AGENTIUM_ABLATION_VARIANT` | `full` | Same semantics as baseline (control). |
| | `no_manifest` | Skip `manifest_declared_tools` enforcement in `ToolRegistry`. |
| | `permissive` | Coerce governance policy outcomes to ALLOW (DLP, access control, budgets unchanged). |

**Never set these in staging/prod.** They exist so `scripts/run_ablation_eval.py`
can drive subprocess matrices without maintaining separate code forks.

Scenario → pytest mapping: [`paper_scenarios.json`](paper_scenarios.json).

## Driver

From repo root (after `pip install -e ".[dev]"`):

```bash
python scripts/run_ablation_eval.py
python scripts/ablation_microbench.py
```

Copy summarized JSON excerpts into [`docs/evidence/`](../../docs/evidence/) after a pinned run.
