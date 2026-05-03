<div align="center">

# Agentium

**面向可治理 LLM 智能体的基础设施（操作系统式控制面视角）**

<br>

[![论文 / arXiv](https://img.shields.io/badge/论文-arXiv%20(待填)-b31b1b.svg)](#论文与引用)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

<br>

[论文与引用](#论文与引用) · [文档索引](#文档索引) · [English README](README.md)

<br>

</div>

---

## 简介

企业级 LLM 智能体在工具调用、通道集成与后台任务上的副作用，需要**可归属、可版本化**的监督。**Agentium** 将其表述为**控制面**问题：不可变 **run manifest**、策略裁决、人在回路门控、可联结的审计字段（`run_id`、`tool_use_id`），以及与前台共享边界的**受治理后台自动化**。配套预印本侧重**架构与协议**，实证为制品内**有边界的**复现与接受性证据，而非榜单式 SOTA 声明。

## 要点

- **分层与契约** — 治理、协调、控制、执行与资源；入口字段（如 `message_disposition`、MCP 执行层级）与可选 grounding 钩子。
- **策略 / 审批 / 审计** — `PolicyEngine`、`ApprovalGate` 与可对接审计下沉协同设计。
- **一致的后台平面** — 定时与事件触发仍受 manifest、租户与策略约束。
- **可复现的保障子集** — 论文复现子集、假设 H1–H6、治理配置档；详见 [`docs/REPRO.md`](docs/REPRO.md)。

## 安装

**环境：** Python **3.11+**。

```bash
pip install -e ".[dev]"
```

若已发布至 PyPI：`pip install agentium`。

## 快速开始

```bash
agentium serve
# 或：
python -m agentium.cli.main serve --host 127.0.0.1 --port 8765
```

可选前端：`cd frontend && npm install && npm run dev`；桌面壳：`npm run dev:desktop`。

更多与英文主文档一致，见 [README.md](README.md)。

## 论文与引用

> **Agentium: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation**  
> Guimin Chen，Jiezhen Zhao

| 资源 | 链接 |
|------|------|
| arXiv 编号 | **`YYYY.NNNNN`**（发布后替换） |
| 摘要页 | `https://arxiv.org/abs/YYYY.NNNNN` |
| PDF | `https://arxiv.org/pdf/YYYY.NNNNN.pdf` |

**稿源说明：** 本公开镜像侧重**可运行代码与复现**（[docs/REPRO.md](docs/REPRO.md)）。完整 LaTeX 与扩展设计说明以 arXiv PDF / 作者侧制品为准，未必全部出现在本仓库路径中。

**引用：** 编号公布后请使用下述 BibTeX 并替换 `eprint` / `url`；在此之前可引用本仓库与上述摘要占位链接。

```bibtex
@misc{chen2026agentium,
  title        = {{Agentium}: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation},
  author       = {Guimin Chen and Jiezhen Zhao},
  year         = {2026},
  eprint       = {YYYY.NNNNN},
  archivePrefix= {arXiv},
  primaryClass = {cs.SE},
  url          = {https://arxiv.org/abs/YYYY.NNNNN},
  note         = {发布后替换 eprint 与 url}
}
```

若 arXiv 元数据主类/交叉类与 `cs.SE` + `cs.AI` 不一致，请以页面为准调整 `primaryClass` 等字段。

## 文档索引

| | |
|---|---|
| 复现 | [`docs/REPRO.md`](docs/REPRO.md) |

更完整的设计索引与 codemap 可能在扩展制品中；本仓库对外维护的论文向入口以 **REPRO** 为主。

## 仓库结构

```text
src/agentium/     # Python 包（API、core、governance、coordination 等）
docs/             # 复现说明（REPRO.md）；完整论述见论文 PDF
frontend/         # Vite，可选 Electron
tests/
```

## 贡献与许可证

贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)；许可证 **Apache-2.0**，见 [LICENSE](LICENSE)。

## 相关工作

与 OpenClaw、DeerFlow、LangGraph、**MCP** 及产品助手的对比与引用边界见预印本 Related Work 与对比表。
