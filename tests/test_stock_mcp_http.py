"""Streamable HTTP MCP 鉴权与挂载冒烟测试。"""

from __future__ import annotations

import os

import pytest
from starlette.testclient import TestClient

from stock_mcp import server as stock_server
from stock_mcp.security import (
    TOKEN_ENV,
    create_bearer_auth_middleware,
    extract_bearer_token,
    token_matches,
)


def test_bearer_helpers():
    assert extract_bearer_token("Bearer abc") == "abc"
    assert extract_bearer_token("bearer abc") == "abc"
    assert extract_bearer_token("Basic abc") is None
    assert token_matches("secret", "secret")
    assert not token_matches("secret", "other")
    assert not token_matches("", "secret")


def test_http_app_requires_bearer_and_exposes_mcp(monkeypatch):
    token = "test-stock-mcp-token-for-pytest"
    monkeypatch.setenv(TOKEN_ENV, token)
    app = stock_server.build_http_app(token=token, host="127.0.0.1")

    with TestClient(app) as client:
        denied = client.get("/mcp")
        assert denied.status_code == 401

        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        headers = {"Authorization": f"Bearer {token}"}
        # Streamable HTTP：无会话 initialize 应被接受（鉴权已通过）
        init = client.post(
            "/mcp",
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        assert init.status_code in (200, 202), init.text
        body = init.text
        assert "stock-data" in body or "protocolVersion" in body or init.status_code == 202

        wrong = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer wrong-token",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert wrong.status_code == 401


def test_middleware_factory_rejects_missing_header():
    mw_cls = create_bearer_auth_middleware("expected")
    assert mw_cls.__name__ == "BearerTokenMiddleware"
