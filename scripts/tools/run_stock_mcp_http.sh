#!/bin/sh

# Stable Streamable HTTP entry for Cursor / Grok Bot (via local reverse tunnel).
# Binds 127.0.0.1 only; requires STOCK_MCP_TOKEN (or the local token file).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
HOST="${STOCK_MCP_HTTP_HOST:-127.0.0.1}"
PORT="${STOCK_MCP_HTTP_PORT:-8766}"
TOKEN_FILE="${STOCK_MCP_TOKEN_FILE:-$HOME/Library/Application Support/stock-data-tunnel/secrets/cursor-mcp-token}"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "stock-data MCP Python is unavailable: $PYTHON_BIN" >&2
    exit 1
fi

if [ -z "${STOCK_MCP_TOKEN:-}" ]; then
    if [ -f "$TOKEN_FILE" ]; then
        STOCK_MCP_TOKEN=$(cat "$TOKEN_FILE")
        export STOCK_MCP_TOKEN
    else
        echo "Missing STOCK_MCP_TOKEN. Mint one with:" >&2
        echo "  $PYTHON_BIN -m stock_mcp.server --mint-token" >&2
        exit 1
    fi
fi

export STOCK_MCP_TOKEN_FILE="$TOKEN_FILE"

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m stock_mcp.server --http --host "$HOST" --port "$PORT"
