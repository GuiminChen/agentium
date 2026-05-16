# Reproducing the paper / evaluation bundle (Track A)

This repository supports a **minimal, automatable** reproducibility path aligned with
[`docs/product/paper-readiness-and-outline.md`](product/paper-readiness-and-outline.md) (Step A).

## Prerequisites

- **Python** 3.11+ (see `requires-python` in [`pyproject.toml`](../pyproject.toml)).
- Install the project in editable mode with dev tools:

```bash
pip install -e ".[dev]"
```

Optional: use **[uv](https://docs.astral.sh/uv/)** for a locked environment — run `uv lock` in the repo root and commit `uv.lock`, then:

```bash
uv sync --all-extras
```

Alternatively, install the pinned subset with:

```bash
pip install -r requirements-repro.txt
pip install -e .
```

## One-command bundle

From the repository root:

```bash
python scripts/reproduce_paper_eval.py
```

This will:

1. Write `artifacts/paper_eval_fingerprint.json` (environment + git revision when available).
2. Run `pytest -m paper` (tests tagged for hypotheses H1–H3).
3. Run `scripts/run_paper_governance_profiles.py` (none / weak / full policy decisions).
4. Run `scripts/run_release_gates.py`.

On success, `artifacts/paper_repro_summary.json` records exit codes and git metadata.

The environment fingerprint’s `extras` includes **`plugins_config_path`** and a JSON **`plugins_runtime`** blob (orchestration / memory / evolution **selection only**, no secrets) from [`configs/runtime_plugins.default.yaml`](../configs/runtime_plugins.default.yaml). Override the file path with **`AGENTIUM_PLUGINS_CONFIG`**.

Flags:

- `--skip-gates` — skip release gates (faster local check).
- `--skip-profiles` — skip the three governance profiles script.

## Three governance profiles

YAML lives under [`configs/paper/`](../configs/paper/). The helper prints JSON:

```bash
python scripts/run_paper_governance_profiles.py
```

## Controlled ablation (paper § empirical minimum)

Deterministic three-way harness **Full / No-manifest / Permissive** plus a micro-benchmark
(D1: no LLM calls). Harness env vars (**default off**):

- **`AGENTIUM_EVALUATION_ABLATION`** must be unset or `false` in normal runs.
- Scripts set `AGENTIUM_EVALUATION_ABLATION=1` and `AGENTIUM_ABLATION_VARIANT` **only inside child pytest subprocesses**.

From the repo root:

```bash
python scripts/run_ablation_eval.py
```

Writes **`artifacts/ablation_<UTC>/`** including `ablation_summary.json`, JUnit XML per variant,
and `microbench.json` (unless `--skip-microbench`). Mapping of H1–H6 → pytest node ids and expected
failure sets: [`configs/ablation/paper_scenarios.json`](../configs/ablation/paper_scenarios.json).

Micro-benchmark only:

```bash
python scripts/ablation_microbench.py --output artifacts/microbench_standalone.json
```

Record a human-readable snapshot following [`docs/evidence/ablation_run_TEMPLATE.md`](evidence/ablation_run_TEMPLATE.md).

## Pinning dependencies

Prefer reproducing with the committed **`uv.lock`** when present (same dependency graph as CI).
If you do not use `uv`, install from `pyproject.toml` and record `pip freeze` or your lockfile
in fork-specific evidence notes.

## Evidence snapshot

After a successful run, copy the summary into
[`docs/evidence/paper_baseline_run.md`](evidence/paper_baseline_run.md) (git SHA, date, and key outcomes).
