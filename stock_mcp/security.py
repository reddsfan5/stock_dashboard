"""STDIO / HTTP 进程级安全边界。"""

from __future__ import annotations

import hmac
import os
import secrets
import socket
from pathlib import Path
from typing import Optional

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8766
DEFAULT_HTTP_PATH = "/mcp"
TOKEN_ENV = "STOCK_MCP_TOKEN"
TOKEN_FILE_ENV = "STOCK_MCP_TOKEN_FILE"
DEFAULT_TOKEN_FILE = Path.home() / "Library/Application Support/stock-data-tunnel/secrets/cursor-mcp-token"


class NetworkAccessDenied(PermissionError):
    """stock-data MCP 不允许访问开放网络。"""


class MissingHttpToken(RuntimeError):
    """HTTP 传输缺少共享 Bearer Token。"""


def install_network_guard() -> None:
    """在专用 MCP 子进程中阻止 IPv4/IPv6 出站连接，保留本地文件与监听。"""
    original_socket = socket.socket

    class LocalOnlySocket(original_socket):
        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                raise NetworkAccessDenied("stock-data MCP 禁止网络请求")
            return super().connect(address)

        def connect_ex(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                raise NetworkAccessDenied("stock-data MCP 禁止网络请求")
            return super().connect_ex(address)

    def blocked_create_connection(*_args, **_kwargs):
        raise NetworkAccessDenied("stock-data MCP 禁止网络请求")

    socket.socket = LocalOnlySocket
    socket.create_connection = blocked_create_connection


def default_token_file() -> Path:
    override = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_TOKEN_FILE


def load_http_token() -> Optional[str]:
    """从环境变量或本地 mode-600 文件读取 HTTP Bearer Token。"""
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token
    path = default_token_file()
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    return None


def require_http_token() -> str:
    token = load_http_token()
    if not token:
        raise MissingHttpToken(
            f"HTTP MCP 需要 {TOKEN_ENV}，或写入 {default_token_file()}"
        )
    return token


def mint_http_token(*, path: Optional[Path] = None, overwrite: bool = False) -> Path:
    """生成随机 Bearer Token，写入本地文件（权限 600），不回显。"""
    target = path or default_token_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return target
    token = secrets.token_urlsafe(32)
    old_umask = os.umask(0o077)
    try:
        target.write_text(token + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
    finally:
        os.umask(old_umask)
    return target


def token_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def create_bearer_auth_middleware(expected_token: str):
    """Starlette/ASGI 中间件：校验 Authorization Bearer。"""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerTokenMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # 健康检查可不鉴权，便于本机探活
            if request.url.path in ("/healthz", "/health"):
                return await call_next(request)
            provided = extract_bearer_token(request.headers.get("authorization"))
            if not token_matches(provided or "", expected_token):
                return JSONResponse(
                    {"error": "unauthorized", "message": "Bearer token required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

    return BearerTokenMiddleware
