<div align="center">

# Agentium

**面向可治理 LLM 智能体的基础设施（操作系统式控制面视角）**

<br>

[![论文 PDF](https://img.shields.io/badge/论文-PDF-b31b1b.svg)](docs/paper/Agentium.pdf)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

<br>

[论文与引用](#论文与引用) · [复现](docs/REPRO.md) · [English README](README.md)

<br>

</div>

---

## 简介

**Agentium** 将企业级 LLM 智能体视为**控制面**问题：**不可变 run manifest**、**策略裁决**、**人在回路门控**、可联结的 **审计字段**（`run_id`、`tool_use_id`），以及与前述周界一致的**受治理后台自动化**。**预印本 PDF：** [docs/paper/Agentium.pdf](docs/paper/Agentium.pdf)。**复现：** [docs/REPRO.md](docs/REPRO.md)。（**`docs/`** 仅放 PDF 与 REPRO。）

## 功能与模块

- **治理与审计** — `PolicyEngine`、`ApprovalGate`、运行域工具白名单、DLP 挂钩、租户隔离；论文向复现见 [docs/REPRO.md](docs/REPRO.md)。
- **LLM Wiki 插件** — 原始 blob、入库作业、Markdown/PDF → wiki、字面/可选语义检索、会话或租户作用域；HTTP 与工具（`wiki.read`）。可选 **`crate`**（`pip install -e ./crate`）。
- **记忆插件** — **`MemoryBackend`**：进程内 / **SQLite**；可选 **Mem0**（`MEM0_API_KEY`，见 [configs/runtime_plugins.default.yaml](configs/runtime_plugins.default.yaml)）。
- **DeepSeek-V4 对话适配器** — 通过 OpenAI 兼容 HTTP 对接 **`deepseek-v4-flash`**（默认）与 **`deepseek-v4-pro`**；支持 **thinking / reasoning effort**、可选 **DSML** 工具调用解析，辅助逻辑在 **`deepseek_v4_agent/`**（如 `model_gate`、`dsml`），入口见 [`deepseek_chat.py`](src/agentium/ai_gateway/deepseek_chat.py)。配置 **`AGENTIUM_DEEPSEEK_API_KEY`**、**`AGENTIUM_CHAT_MODEL`**、可选 **`AGENTIUM_DEEPSEEK_BASE_URL`**；其它开关见 [`.env.example`](.env.example) 与 [`settings.py`](src/agentium/app/settings.py) 中 **`AGENTIUM_DEEPSEEK_*`**。
- **编排插件** — **原生** DAG；YAML 内为 **LangGraph** 等保留扩展位。
- **进化插件** — 有界轨迹进入 **治理队列**（如 `ProposalQueue`）；`evolution.*` 下 **Hermes 类**配置。
- **插件配置** — [configs/runtime_plugins.default.yaml](configs/runtime_plugins.default.yaml)，**`AGENTIUM_PLUGINS_CONFIG`** 可覆写路径。

## 安装

**Python 3.11+**

```bash
pip install -e ".[dev]"
```

## 快速开始

```bash
agentium serve
python -m agentium.cli.main serve --host 127.0.0.1 --port 8765
```

可选前端：`cd frontend && npm install && npm run dev`；桌面壳：`npm run dev:desktop`。

## 论文与引用

> **Agentium: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation** — Guimin Chen，Jiezhen Zhao。

**PDF：** [docs/paper/Agentium.pdf](docs/paper/Agentium.pdf)（26 页含参考文献与附录；3 张 TikZ 示意图）。**arXiv** 上线后可补充 `eprint` 并将 `url` 改为 `https://arxiv.org/abs/…`。

```bibtex
@misc{chen2026agentium,
  title        = {{Agentium}: A Governable Agent Operating System; Immutable Run Contracts, Policy Gates, and Auditable Automation},
  author       = {Guimin Chen and Jiezhen Zhao},
  year         = {2026},
  url          = {https://github.com/GuiminChen/agentium/blob/main/docs/paper/Agentium.pdf},
  note         = {仓库内预印本 PDF；arXiv 公布后请增加 eprint/url}
}
```

## 仓库结构

```text
src/agentium/
docs/
  REPRO.md              # 复现说明
  paper/Agentium.pdf    # 预印本 PDF
frontend/
tests/
```

## 贡献与许可证

[CONTRIBUTING.md](CONTRIBUTING.md) · **Apache-2.0** — [LICENSE](LICENSE)
