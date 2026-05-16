<div align="center">

# Agentium

**Governable LLM agent infrastructure with OS-style control planes**

<br>

[![Paper (PDF)](https://img.shields.io/badge/paper-PDF-b31b1b.svg)](docs/paper/Agentium.pdf)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

<br>

[Paper & citation](#paper--citation) · [Replication](docs/REPRO.md) · [中文说明](README.zh.md)

<br>

</div>

---

## Overview

**Agentium** treats enterprise LLM agents as a **control-plane** problem: **immutable run manifests**, **policy adjudication**, **human gates**, **joinable audit fields** (`run_id`, `tool_use_id`), and a **governed background plane** on the same perimeter as interactive work. **Preprint PDF:** [docs/paper/Agentium.pdf](docs/paper/Agentium.pdf). **Replication:** [docs/REPRO.md](docs/REPRO.md). (`docs/` stays small: PDF + REPRO only.)

## Capabilities

- **Governance & audit** — `PolicyEngine`, `ApprovalGate`, run-scoped tool allowlists, DLP hooks, tenant isolation; reproducible paper subset ([docs/REPRO.md](docs/REPRO.md)).
- **LLM Wiki plugin** — Raw blob store, ingest jobs, markdown/PDF → wiki pages, **literal / optional semantic** search, session or tenant scope; HTTP APIs and tools (`wiki.read`). Optional in-repo **`crate`** package (`pip install -e ./crate`).
- **Memory plugin** — Pluggable **`MemoryBackend`**: in-process / **SQLite** built-ins; optional **Mem0**-style lane via `MEM0_API_KEY` in [configs/runtime_plugins.default.yaml](configs/runtime_plugins.default.yaml).
- **DeepSeek-V4 chat adapter** — OpenAI-compatible chat completions for **`deepseek-v4-flash`** (default) and **`deepseek-v4-pro`**, with V4 **thinking** / reasoning-effort handling, optional **DSML** tool-call extraction, and a small **`deepseek_v4_agent/`** helper package (`model_gate`, `dsml`, …) used from [`deepseek_chat.py`](src/agentium/ai_gateway/deepseek_chat.py). Set **`AGENTIUM_DEEPSEEK_API_KEY`**, **`AGENTIUM_CHAT_MODEL`**, optional **`AGENTIUM_DEEPSEEK_BASE_URL`** — see [`.env.example`](.env.example) and [`settings.py`](src/agentium/app/settings.py) for `AGENTIUM_DEEPSEEK_*` toggles.
- **Orchestration plugin** — **Native** DAG-style execution; **LangGraph** slot reserved in the same YAML.
- **Evolution plugin** — Bounded trajectories feed **governance queues** (e.g. `ProposalQueue`); **Hermes-class** hooks under `evolution.*` (no silent overwrite of production policy).
- **Plugin configuration** — Single file [configs/runtime_plugins.default.yaml](configs/runtime_plugins.default.yaml); override with **`AGENTIUM_PLUGINS_CONFIG`**. Secrets only via `*_from_env`.

## Installation

**Python 3.11+**

```bash
pip install -e ".[dev]"
```

## Quick start

```bash
agentium serve
python -m agentium.cli.main serve --host 127.0.0.1 --port 8765
```

Optional UI: `cd frontend && npm install && npm run dev` (desktop: `npm run dev:desktop`).

## Paper & citation

> **Agentium: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation** — Guimin Chen, Jiezhen Zhao.

**PDF:** [docs/paper/Agentium.pdf](docs/paper/Agentium.pdf) (26 pages including references and appendix; three TikZ figures). **arXiv** — when listed, you may add `eprint` and point `url` to `https://arxiv.org/abs/…`.

```bibtex
@misc{chen2026agentium,
  title        = {{Agentium}: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation},
  author       = {Guimin Chen and Jiezhen Zhao},
  year         = {2026},
  url          = {https://github.com/GuiminChen/agentium/blob/main/docs/paper/Agentium.pdf},
  note         = {Preprint PDF in repository; add arXiv eprint/url when announced}
}
```

## Repository structure

```text
src/agentium/
docs/
  REPRO.md              # replication guide
  paper/Agentium.pdf    # preprint PDF
frontend/
tests/
```

## Contributing · License

[CONTRIBUTING.md](CONTRIBUTING.md) · **Apache-2.0** — [LICENSE](LICENSE)
