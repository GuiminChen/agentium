<div align="center">

# Agentium

**Governable LLM agent infrastructure with OS-style control planes**

<br>

[![Paper / arXiv](https://img.shields.io/badge/paper-arXiv%20(TBA)-b31b1b.svg)](#paper--citation)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

<br>

[Paper & citation](#paper--citation) · [Documentation](#documentation) · [中文说明](README.zh.md)

<br>

</div>

---

## Overview

Enterprise LLM agents amplify risk when tools, channels, and background jobs lack **attributable, versioned supervision**. **Agentium** treats that gap as a **control-plane** problem: **immutable run manifests**, **policy adjudication**, **human gates**, **joinable audit fields** (`run_id`, `tool_use_id`), and a **governed background plane** subject to the same perimeter as interactive work. The accompanying preprint is **architecture- and protocol-forward**, with **scoped empirical evidence** from the open artefact (tagged tests, governance profiles, scripted ablations)—not headline benchmark claims.

## Highlights

- **Planes & contracts** — Governance, coordination, control, execution, and resources; ingress contracts (e.g. `message_disposition`, MCP execution tier) and optional grounding hooks.
- **Policy + approvals + audit** — Co-designed chain: `PolicyEngine`, `ApprovalGate`, lineage-friendly sinks.
- **Background without regime change** — Triggers and daemons stay under manifests, tenancy, and policy (no permissive “shadow loop”).
- **Reproducible assurance slice** — Paper replication subset, hypotheses H1–H6, profiles (`none` / `weak` / `full`); see [docs/REPRO.md](docs/REPRO.md).

## Installation

**Requirements:** Python **3.11+**.

From a clone of this repository:

```bash
pip install -e ".[dev]"
```

PyPI (when published):

```bash
pip install agentium
```

## Quick start

Start the HTTP control plane (default host/port depend on your build; override as needed):

```bash
agentium serve
# Windows / restricted script policy:
python -m agentium.cli.main serve --host 127.0.0.1 --port 8765
```

Optional UI:

```bash
cd frontend && npm install && npm run dev
# optional desktop shell:
npm run dev:desktop
```

For evaluation / paper-style runs, see **[docs/REPRO.md](docs/REPRO.md)**.

## Paper & citation

> **Agentium: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation**  
> Guimin Chen, Jiezhen Zhao


| Resource   | Link                                                         |
| ---------- | ------------------------------------------------------------ |
| arXiv (id) | **`YYYY.NNNNN`** — replace after public announcement         |
| Abstract   | `https://arxiv.org/abs/YYYY.NNNNN`                           |
| PDF        | `https://arxiv.org/pdf/YYYY.NNNNN.pdf`                       |

**Sources:** this public mirror focuses on **code and replication** ([docs/REPRO.md](docs/REPRO.md)). Full LaTeX and extended design notes ship with the arXiv PDF / author artefact, not necessarily every path in this tree.

**Please cite** the arXiv entry once the id is live (BibTeX below). Until then, you may cite this repository and the preprint abstract URL placeholder above.

```bibtex
@misc{chen2026agentium,
  title        = {{Agentium}: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation},
  author       = {Guimin Chen and Jiezhen Zhao},
  year         = {2026},
  eprint       = {YYYY.NNNNN},
  archivePrefix= {arXiv},
  primaryClass = {cs.SE},
  url          = {https://arxiv.org/abs/YYYY.NNNNN},
  note         = {Update eprint and url after arXiv announcement}
}
```

Use the posted arXiv primary/cross categories if they differ (e.g. `cs.AI` cross-list).

## Documentation

|             |                                                  |
| ----------- | ------------------------------------------------ |
| Replication | [docs/REPRO.md](docs/REPRO.md)                   |

A fuller design index and codemap may live in extended artefacts; the maintained public entry point for the **paper-style evaluation bundle** is **REPRO**.


## Repository structure

```text
src/agentium/     # Python package (API, core, governance, coordination, …)
docs/             # replication guide (REPRO.md); see paper PDF for full write-up
frontend/         # Vite + optional Electron UI
tests/            # unit / integration / e2e
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).

## Related work (in the paper)

OpenClaw, DeerFlow, LangGraph, **MCP**, and product assistants are positioned with explicit scope in the preprint’s Related Work and comparison table—see the PDF for citations and limitations.