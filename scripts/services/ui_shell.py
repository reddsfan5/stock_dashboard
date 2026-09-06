# -*- coding: utf-8 -*-
"""Shared UI shell helpers for interactive HTML pages."""

from __future__ import annotations

HEAD_ASSETS = (
    '<link rel="stylesheet" href="/assets/app.css">\n'
    '<script src="/assets/app-shell.js" defer></script>'
)


def shell_mount_script(active: str) -> str:
    """Inline mount snippet; prefer data-active on #app-shell + deferred JS."""
    key = (active or "").replace("\\", "\\\\").replace("'", "\\'")
    return (
        f"<script>window.StockAppShell&&StockAppShell.mount({{active:'{key}'}});</script>"
    )


def shell_host_html(active: str) -> str:
    key = (active or "").replace('"', "&quot;")
    return f'<div id="app-shell" data-active="{key}"></div>'


def mobile_redirect(target):
    import json
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>打开工作台</title><a href="'+target+'">打开工作台</a><script>location.replace('+json.dumps(target)+'+location.search+location.hash)</script></html>'
