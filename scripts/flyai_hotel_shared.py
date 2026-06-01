"""飞猪酒店相关共享工具：供 enrich_hotel_intro_from_flyai 与 image_fallback_chain 共用。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any


def parse_hotel_names(description: str) -> list[str]:
    """从住宿 description 中解析「备选酒店：」或「【拟定酒店】」后的酒店名列表。"""
    text = (description or "").strip()
    if not text:
        return []
    if "备选酒店：" in text:
        body = text.split("备选酒店：", 1)[1]
    elif "【拟定酒店】" in text:
        body = text.split("【拟定酒店】", 1)[1].split("\n")[0]
    else:
        return []
    body = re.split(r"[。\n]", body, maxsplit=1)[0]
    body = re.sub(r"（[^（）]*）", "", body)
    body = body.split("（")[0]
    return [p.strip() for p in re.split(r"[、,，]", body) if p.strip()]


def run_flyai(
    city: str,
    key_words: str,
    check_in: str,
    check_out: str,
    timeout: int,
) -> dict[str, Any] | None:
    """执行 ``flyai search-hotels``，成功时返回 ``data`` 对象，否则 None。"""
    kw = (key_words or "").strip()[:48]
    cmd_base = [
        "flyai",
        "search-hotels",
        "--dest-name",
        city,
        "--check-in-date",
        check_in,
        "--check-out-date",
        check_out,
    ]
    attempts: list[list[str]] = []
    if kw:
        attempts.append(cmd_base + ["--key-words", kw])
    attempts.append(cmd_base)

    for cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            print("未找到 flyai，请先执行：npm i -g @fly-ai/flyai-cli", file=sys.stderr)
            return None
        except subprocess.TimeoutExpired:
            print(f"flyai 超时: {city} cmd={cmd}", file=sys.stderr)
            continue
        if proc.returncode != 0:
            continue
        raw = (proc.stdout or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        inner = data.get("data")
        if isinstance(inner, dict):
            il = inner.get("itemList")
            if isinstance(il, list) and len(il) > 0:
                return inner
        continue
    return None
