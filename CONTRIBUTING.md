# Contributing to Agentium

## Scope

- **Code** lives under `src/agentium/` per project layout rules.
- **Tests**: `tests/unit`, `tests/integration`, `tests/e2e`.
- **Docs**: product/architecture/runbooks under `docs/`; avoid duplicating truths across READMEs.

## Local checks

```bash
python -m pytest tests/unit tests/integration -q --tb=line
python scripts/validate_runtime_policies.py
```

Windows (optional conda env + repo paths) matches [docs/runbooks/phase1-acceptance.md](docs/runbooks/phase1-acceptance.md).

## Pull requests

- Link behavioral changes to **tests** or **runbooks**.
- Update [phase3-implementation-matrix.md](docs/product/phase3-implementation-matrix.md) when a Phase 3 checklist row moves state.

## Production case study (sanitized) template

When sharing a retrospective:

1. **Context**: industry (generic), scale band, deployment model (no customer names unless cleared).
2. **Policy snapshot**: version / bundle id (non-secret).
3. **Signals**: safety incidents = 0/ N; p95 turn latency; cost/token envelope.
4. **Failures & rollbacks**: what broke; how rollback was exercised; audit event types cited.
5. **Limitations**: what the case does **not** prove.

Do **not** paste secrets, raw PII, or unsigned policy bundles.
