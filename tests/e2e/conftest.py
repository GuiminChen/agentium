"""E2E test fixtures: spin up bootstrap container + HTTP server in-process."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import json

import pytest

from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.app import build_runtime_container, load_settings
from agentium.tools.tool_registry import ToolSpec


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class HttpClient:
    """Tiny stdlib HTTP client returning (status, body)."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def post(self, path: str, body: Dict[str, Any], headers: Dict[str, str]):
        data = json.dumps(body).encode("utf-8")
        request = Request(
            url=self._base_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def get(self, path: str, headers: Dict[str, str] | None = None):
        request = Request(
            url=self._base_url + path,
            method="GET",
            headers=headers or {},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


_E2E_POLICY = """version: e2e-test
default_decision: deny
default_reason: e2e default deny
rules:
  - id: allow-echo
    decision: allow
    reason: allow echo
    tools: [echo]
  - id: allow-leak
    decision: allow
    reason: allow leak so DLP can inspect output
    tools: [leak]
"""


@pytest.fixture()
def http_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(_E2E_POLICY, encoding="utf-8")
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_AUDIT_BACKEND", "memory")
    monkeypatch.setenv("AGENTIUM_APPROVAL_BACKEND", "memory")
    monkeypatch.setenv("AGENTIUM_POLICY_PATH", str(policy_path))
    settings = load_settings()
    container = build_runtime_container(settings)
    container.tool_registry.register(
        ToolSpec(
            name="echo",
            capabilities=["read_only"],
            risk_level="low",
            handler=lambda args: {"echo": args},
        )
    )
    container.tool_registry.register(
        ToolSpec(
            name="risky",
            capabilities=["external_write"],
            risk_level="high",
            handler=lambda args: {"sent": True},
        )
    )
    container.tool_registry.register(
        ToolSpec(
            name="leak",
            capabilities=["read_only"],
            risk_level="low",
            handler=lambda args: {
                "body": "-----BEGIN OPENSSH PRIVATE KEY-----\nABC\n-----END OPENSSH PRIVATE KEY-----"
            },
        )
    )
    port = _find_free_port()
    resources = HTTPControlPlaneResources(
        run_message_store=container.run_message_store,
        chat_session_store=container.chat_session_store,
        chat_turn_service=container.chat_turn_service,
    )
    server = build_http_server(
        api=container.api,
        host="127.0.0.1",
        port=port,
        manifest_policy=container.manifest_policy,
        audit_sink=container.audit_sink,
        resources=resources,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    client = HttpClient(f"http://127.0.0.1:{port}")
    try:
        yield client, container
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        container.shutdown()
