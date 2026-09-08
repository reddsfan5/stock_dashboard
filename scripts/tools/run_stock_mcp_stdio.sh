#!/bin/sh

# Stable STDIO entry point for tunnel-client. Keep protocol output on stdout;
# Python/MCP diagnostics must continue to use stderr.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "stock-data MCP Python is unavailable: $PYTHON_BIN" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m stock_mcp.server
