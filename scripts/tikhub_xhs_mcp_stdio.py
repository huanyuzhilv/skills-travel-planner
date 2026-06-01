#!/usr/bin/env python3
"""TikHub 小红书 MCP（stdio）：直连 api.tikhub.io REST。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from repo_dotenv import load_repo_dotenv  # noqa: E402
from tikhub_xhs_client import (  # noqa: E402
    TikHubXhsClient,
    normalize_detail_payload,
    parse_search_results,
)

load_repo_dotenv(_REPO_ROOT)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "xiaohongshu_search_notes",
        "description": "按关键词搜索小红书笔记（TikHub app_v2/search_notes）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "page": {"type": "integer", "description": "页码，从 1 开始", "default": 1},
                "sort_type": {"type": "string", "description": "排序方式"},
                "note_type": {"type": "string", "description": "笔记类型筛选"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "xiaohongshu_get_note_info",
        "description": "获取单条小红书笔记详情（TikHub app/get_note_info）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "description": "笔记 ID"},
            },
            "required": ["note_id"],
        },
    },
]


def _reply(request_id: Any, result: Dict[str, Any] | None = None, error: str | None = None) -> None:
    msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error:
        msg["error"] = {"code": -32603, "message": error}
    else:
        msg["result"] = result
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def _call_tool(name: str, arguments: Dict[str, Any]) -> str:
    client = TikHubXhsClient()
    if name == "xiaohongshu_search_notes":
        keyword = (arguments.get("keyword") or "").strip()
        if not keyword:
            raise ValueError("keyword 不能为空")
        raw = client.search_notes(
            keyword,
            page=int(arguments.get("page") or 1),
            sort_type=arguments.get("sort_type"),
            note_type=arguments.get("note_type"),
        )
        payload = {
            "keyword": keyword,
            "page": int(arguments.get("page") or 1),
            "articles": parse_search_results(raw),
        }
    elif name == "xiaohongshu_get_note_info":
        note_id = (arguments.get("note_id") or "").strip()
        if not note_id:
            raise ValueError("note_id 不能为空")
        raw = client.get_note_info(note_id)
        payload = normalize_detail_payload(raw)
    else:
        raise ValueError(f"未知工具: {name}")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _handle(request: Dict[str, Any]) -> None:
    method = request.get("method")
    params = request.get("params") or {}
    req_id = request.get("id")

    if method == "initialize":
        _reply(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tikhub-xhs-mcp", "version": "1.0.0"},
            },
        )
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _reply(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        try:
            text = _call_tool(params.get("name", ""), params.get("arguments") or {})
            _reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            _reply(req_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return

    if req_id is not None:
        _reply(req_id, error=f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply(None, error=f"Parse error: {exc}")
            continue
        if "id" not in req:
            _handle(req)
            continue
        _handle(req)


if __name__ == "__main__":
    main()
