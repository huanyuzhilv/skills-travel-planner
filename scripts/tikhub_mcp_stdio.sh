#!/usr/bin/env bash
# Cursor / Claude Desktop stdio 入口：加载密钥后启动 TikHub 小红书 MCP。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROXY="${TIKHUB_MCP_PROXY:-${HOME}/.local/share/tikhub-mcp/TikHub_MCP_Mac/tikhub_mcp_proxy_mac.py}"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi
if [[ -f "${HOME}/.tikhub.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${HOME}/.tikhub.env"
  set +a
fi

XHS_MCP="${ROOT}/scripts/tikhub_xhs_mcp_stdio.py"
if [[ -f "${XHS_MCP}" ]]; then
  exec python3 "${XHS_MCP}"
fi

export TIKHUB_MCP_SERVER_URL="${TIKHUB_MCP_SERVER_URL:-https://mcp.tikhub.io}"
exec python3 "${PROXY}"
