"""STDIO 进程级安全边界。"""

from __future__ import annotations

import socket


class NetworkAccessDenied(PermissionError):
    """stock-data MCP 不允许访问开放网络。"""


def install_network_guard() -> None:
    """在专用 MCP 子进程中阻止 IPv4/IPv6 连接，保留本地文件与 STDIO。"""
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
